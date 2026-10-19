# Command line: the same engine as the web UI.
#
#   dich <file | folder | url> [--to vi,ja]       subtitles + translation next to each video
#   dich <...> --dub                              + dubbed voice track and <name>.dub.<lang>.<ext>
#   dich serve                                    web UI + workers on http://127.0.0.1:7860
#   dich worker --lanes cpu                       more workers (another terminal)
#   dich drivers | templates | voices
import argparse
import os
import sys
from pathlib import Path

from dich import settings

COMMANDS = ("run", "serve", "ui", "worker", "drivers", "templates", "voices")


def parse_value(text):
    import json
    try:
        return json.loads(text)
    except ValueError:
        return text


def build_parser():
    parser = argparse.ArgumentParser(prog="dich", description="Phụ đề, dịch và lồng tiếng video. "
                                     "`dich <path>` = `dich run <path>`.")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="xử lý file / thư mục / URL ngay trong terminal")
    run.add_argument("sources", nargs="+", help="video, audio, .srt, thư mục hoặc URL")
    run.add_argument("--to", default="vi", help="ngôn ngữ đích, cách nhau dấu phẩy (mặc định: vi)")
    run.add_argument("--template", "-t", help="default | subs-only | dub-review | transcribe (xem `dich templates`)")
    run.add_argument("--dub", action="store_true", help="lồng tiếng và ghép video (template default)")
    run.add_argument("--recursive", "-r", action="store_true", help="quét cả thư mục con")
    run.add_argument("--engine", "-e", help=f"driver dịch: local | openai | openai-batch | claude (mặc định: {settings.ENGINE})")
    run.add_argument("--language", help="ngôn ngữ nói, VD en (mặc định: tự nhận)")
    run.add_argument("--voice", help="giọng lồng tiếng: source (clone từ video) hoặc giọng mẫu")
    run.add_argument("--ref-audio", type=Path, help="clone giọng từ clip 3–8 giây")
    run.add_argument("--set", action="append", default=[], metavar="STAGE.PARAM=VALUE",
                     help="tham số bất kỳ, VD --set dub.max_speed=1.5 --set mux.add_track=true")
    run.add_argument("--use-srt", action="store_true", help="dùng <tên>.srt / <tên>.vi.srt có sẵn cạnh video")
    run.add_argument("--no-export", action="store_true", help="không ghi kết quả cạnh file gốc")

    for name in ("serve", "ui"):
        serve = commands.add_parser(name, help="giao diện web + workers" if name == "serve" else "như serve")
        serve.add_argument("--root", type=Path, default=settings.UI_ROOT,
                           help=f"thư mục UI được đọc/ghi (mặc định: {settings.UI_ROOT}, $DICH_UI_ROOT)")
        serve.add_argument("--host", default=settings.UI_HOST,
                           help=f"địa chỉ lắng nghe (mặc định: {settings.UI_HOST}, $DICH_UI_HOST); 0.0.0.0 để máy khác"
                                " truy cập — không có đăng nhập, nên dùng qua Tailscale")
        serve.add_argument("--port", type=int, default=settings.UI_PORT)
        serve.add_argument("--no-browser", action="store_true")

    worker = commands.add_parser("worker", help="chạy thêm workers (cùng database)")
    worker.add_argument("--lanes", default="cpu,net", help="cpu,net,gpu,interactive,bulk_download")
    worker.add_argument("--count", type=int, default=1, help="số process mỗi lane")

    commands.add_parser("drivers", help="liệt kê drivers và có dùng được không")
    commands.add_parser("templates", help="liệt kê templates")
    commands.add_parser("voices", help="liệt kê giọng mẫu lồng tiếng")
    return parser


def overrides_of(args):
    overrides = {}

    def put(stage, key, value):
        overrides.setdefault(stage, {}).setdefault("params", {})[key] = value

    if args.engine:
        overrides.setdefault("translate", {})["driver"] = args.engine
    if args.language:
        put("asr", "language", args.language)
    if args.voice:
        put("dub", "voice", args.voice)
    if args.ref_audio:
        put("dub", "ref_audio", str(args.ref_audio.resolve()))
    for item in args.set:
        name, sep, value = item.partition("=")
        stage, dot, key = name.partition(".")
        if not (sep and dot):
            sys.exit(f"--set {item}: cần dạng stage.param=value")
        if key in ("driver", "enabled", "gate"):
            overrides.setdefault(stage, {})[key] = parse_value(value)
        else:
            put(stage, key, parse_value(value))
    return overrides


def expand(sources, recursive):
    from dich.api.jobs import find_media
    from dich.core.jobs import is_url
    from dich.media import is_media, is_subtitle

    found = []
    for source in sources:
        if is_url(source):
            found.append(source)
            continue
        path = Path(source).expanduser().resolve()
        if not path.exists():
            sys.exit(f"Không tìm thấy {path}")
        if path.is_dir():
            found += find_media(path, recursive)
        elif is_media(path.name) or is_subtitle(path.name):
            found.append(path)
        else:
            sys.exit(f"{path.name} không phải video/audio hay .srt")
    if not found:
        sys.exit("Không có video/audio nào")
    return found


def echo(task, level, text):
    if level == "progress":
        done, _, total = text.partition("/")
        print(f"\r\033[K  [{task['key']}] {text}", end="\n" if done == total else "", flush=True)
    else:
        mark = {"warn": "⚠ ", "error": "✗ "}.get(level, "")
        print(f"\r\033[K  [{task['key']}] {mark}{text}", flush=True)


def run(args):
    from dich.core.jobs import create_job
    from dich.queue.sqlite import Store
    from dich.runtime.local import run_job

    template = args.template or ("default" if args.dub else "subs-only")
    langs = [lang.strip() for lang in args.to.split(",") if lang.strip()]
    store = Store()
    failed = 0
    for index, source in enumerate(expand(args.sources, args.recursive), 1):
        try:
            job_id = create_job(store, source, langs, template, overrides_of(args), export=not args.no_export,
                                use_subtitles=args.use_srt)
        except Exception as error:   # unknown driver, bad params
            sys.exit(str(error).strip("'"))
        print(f"[{index}] {Path(str(source)).name} ({job_id})")
        try:
            job = run_job(store, job_id, echo)
        except KeyboardInterrupt:
            for task in store.tasks_of(job_id):
                if task["status"] == "running":
                    store.cancelled(task["id"], (store.attempts_of(task["id"]) or [{"id": None}])[0]["id"])
            store.cancel_job(job_id)
            sys.exit("\nĐã dừng")
        for task in job["tasks"]:
            if task["status"] == "skipped" and task["message"] and "nguồn" not in task["message"]:
                print(f"  [{task['key']}] bỏ qua: {task['message']}")
            if task["status"] == "failed":
                failed += 1
            for path in task["exports"]:
                print(f"  → {path}")
        if job["status"] == "waiting_review":
            print("  Đang chờ duyệt: mở `dich serve` để sửa phụ đề rồi bấm Tiếp tục")
    sys.exit(1 if failed else 0)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv = ["run", *argv]
    args = build_parser().parse_args(argv)

    if args.command in ("serve", "ui"):
        from dich.api.app import launch
        launch(args.root, args.port, args.host, open_browser=not args.no_browser)
    elif args.command == "worker":
        from dich.runtime.supervisor import Supervisor
        lanes = [lane.strip() for lane in args.lanes.split(",") if lane.strip()]
        print(f"Workers: {', '.join(lanes)} × {args.count}  (Ctrl+C để dừng)")
        Supervisor(workers={lane: args.count for lane in lanes}).start().wait()
    elif args.command == "drivers":
        from dich import drivers
        for (stage, name), cls in sorted(drivers.load_all().items()):
            problem = cls.available()
            print(f"{stage:10} {name:10} {'✓' if not problem else '✗ ' + problem}  {cls.title}")
    elif args.command == "templates":
        from dich.core.template import load_templates
        for template in load_templates().values():
            stages = " → ".join(s.stage + ("" if s.enabled else "(tắt)") for s in template.stages)
            print(f"{template.name:12} {template.title}\n{'':12} {stages}")
    elif args.command == "voices":
        from dich.drivers.dub.vieneu import SOURCE_VOICE, VieneuDub
        print(f"{SOURCE_VOICE}  (mặc định: clone giọng người nói trong video)")
        for voice in VieneuDub().voices():
            print(voice["label"])
    else:
        run(args)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
