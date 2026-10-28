# Runs one claimed task: resolve its inputs by slot, reuse a cached result with the same fingerprint or
# call the driver, record the artifacts, export the results. Shared by the worker processes and
# `dich run` (in-process).
import os
import threading
import traceback
from pathlib import Path

from dich import drivers
from dich.logging import system
from dich.logging.events import Skip, UserError
from dich.logging.trace import error_code
from dich.runtime.context import TaskContext
from dich.storage.artifacts import describe, fingerprint, reuse
from dich.storage.export import export

HEARTBEAT = 1.0   # seconds between lease renewals / stop checks


class Engine:
    def __init__(self, store, capture=True, echo=None, on_cancel=None):
        """capture: send fd 1/2 to the attempt's system.log (worker processes only). echo(task, level, text)
        also gets the pipeline log (CLI). on_cancel(task, attempt) is called from the heartbeat thread when
        the user stops the running task; the worker exits there, so the driver needs no stop checks."""
        self.store = store
        self.capture = capture
        self.echo = echo
        self.on_cancel = on_cancel
        self.instances = {}

    def instance(self, cls):
        key = (cls.stage, cls.name)
        if key not in self.instances:
            driver = cls()
            driver.load()
            self.instances[key] = driver
        return self.instances[key]

    def _heartbeat(self, task, attempt, stop):
        while not stop.wait(HEARTBEAT):
            try:
                if self.store.heartbeat(task["id"]) and self.on_cancel:
                    self.on_cancel(task, attempt)
            except Exception:
                traceback.print_exc()

    def run(self, task):
        store = self.store
        try:
            cls = drivers.get(task["stage"], task["driver"])
            params = cls.Params(**task["params"])
            inputs = store.inputs_of(task)
        except Exception as error:   # driver uninstalled, bad params from an old template
            attempt = store.start_attempt(task, pid=os.getpid())
            store.fail(task["id"], attempt["id"], f"Không chạy được: {error}", repr(error))
            return "failed"
        lost = [name for name, art in inputs.items() if art is None or not Path(art["path"]).exists()]
        print_fp = fingerprint(cls, params.model_dump(mode="json"), inputs) if not lost else None
        fresh = task.get("fresh", False)
        cached = store.find_cached(print_fp) if print_fp and getattr(cls, "cache", True) and not fresh else None
        attempt = store.start_attempt(task, origin="cache" if cached else "driver", fingerprint=print_fp,
                                      pid=os.getpid(), params=params.model_dump(mode="json"))
        ctx = TaskContext(store, task, attempt, self.echo, fresh=fresh)
        if fresh:
            ctx.drop_checkpoint()
            ctx.log.info("Chạy mới hoàn toàn, không dùng cache")
        stop = threading.Event()
        threading.Thread(target=self._heartbeat, args=(task, attempt, stop), daemon=True).start()
        log_path = Path(attempt["dir"]) / "system.log"
        try:
            if lost:
                raise UserError(f"Thiếu đầu vào {', '.join(lost)}: hãy chạy lại bước trước")
            if cached:
                rows = reuse(cached, task, ctx.outdir)
                ctx.log.info("Dùng lại kết quả đã có (cùng đầu vào và tham số)")
            else:
                if self.capture:
                    with system.capture(log_path):
                        outputs = self._call(cls, ctx, inputs, params)
                else:
                    outputs = self._call(cls, ctx, inputs, params)
                rows = describe(cls, task, outputs, ctx.outdir)
            store.complete(task["id"], attempt["id"], rows, self._message(ctx))
            ctx.drop_checkpoint()
            self._export(task)
            return "done"
        except Skip as error:
            ctx.log.warn(str(error))
            store.skip(task["id"], attempt["id"], str(error))
            return "skipped"
        except UserError as error:
            ctx.log.error(str(error))
            store.fail(task["id"], attempt["id"], str(error))
            return "failed"
        except Exception as error:
            code = error_code(attempt["span_id"])
            with log_path.open("a", encoding="utf-8") as file:
                file.write(f"[{code}] " + traceback.format_exc())
            message = f"Lỗi hệ thống (mã {code})"
            ctx.log.error(message, code=code)
            store.fail(task["id"], attempt["id"], message, f"{type(error).__name__}: {error}")
            return "failed"
        finally:
            stop.set()
            ctx.cleanup()

    def _call(self, cls, ctx, inputs, params):
        if cls.available():
            raise UserError(cls.available())
        driver = self.instance(cls)
        return driver.run(ctx, inputs, params)

    @staticmethod
    def _message(ctx):
        """The last info line of the attempt, as the task's summary."""
        events = ctx.store.attempt_events(ctx.attempt["id"], limit=20)
        lines = [e["message"] for e in events if e["type"] == "log" and e["level"] == "info" and e["message"]]
        return lines[-1] if lines else None

    def _export(self, task):
        store = self.store
        job = store.job_row(task["job_id"])
        if not job["export_dir"]:
            return
        task = store.task(task["id"])
        tasks = store.tasks_of(task["job_id"])
        later = [t for t in tasks if t["position"] > task["position"] and t["status"] != "skipped"]
        source = job["source"]
        if source.startswith(("http://", "https://")):
            _producer, video = store.slot_artifact(task["job_id"], "media.video")
            source = video["path"] if video else "video.mp4"
        written = []
        for name, slot in task["outputs"].items():
            if task["stage"] == "source" or any(slot in t["outputs"].values() for t in later):
                continue
            artifact = store.output_of(task, slot)
            try:
                path = export(artifact, slot, job["export_dir"], source) if artifact else None
            except OSError as error:
                store.event(task["job_id"], "log", f"Không xuất được {slot}: {error}", level="warn",
                            task_id=task["id"])
                continue
            if path:
                written.append(path)
        if written:
            store.set_exports(task["id"], written)
            store.event(task["job_id"], "log", "Đã xuất " + ", ".join(p.name for p in written), task_id=task["id"],
                        attempt_id=task["current_attempt"])

    # --- calls (voice preview, voice list): answered by the interactive worker ---

    def answer(self, call):
        try:
            dub = self.instance(drivers.get("dub", "vieneu"))
            if call["kind"] == "voices":
                result = dub.voices()
            elif call["kind"] == "preview":
                result = dub.preview(call["args"], self.store.data_dir / "previews")
            else:
                raise UserError(f"Không biết lệnh {call['kind']}")
            self.store.answer_call(call["id"], result)
        except UserError as error:
            self.store.answer_call(call["id"], error=str(error))
        except ImportError:
            self.store.answer_call(call["id"], error="Chưa cài lồng tiếng: uv sync --extra tts")
        except Exception as error:
            traceback.print_exc()
            self.store.answer_call(call["id"], error=f"{type(error).__name__}: {error}")


def export_task(store, task):
    """Export a task's current outputs again (after an edit by hand)."""
    Engine(store)._export(task)
