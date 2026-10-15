# Presets: named advanced options for new jobs, kept in <data>/presets.json (small, edited by hand too).
import json
import os

from fastapi import APIRouter, HTTPException

from dich.api.models import Preset, PresetBody


def router(ctx):
    api = APIRouter()
    path = ctx.data_dir / "presets.json"

    def load():
        try:
            return {p["name"]: Preset(**p) for p in json.loads(path.read_text(encoding="utf-8"))}
        except FileNotFoundError:
            return {}

    def store(presets):
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_text(json.dumps([p.model_dump(exclude_none=True) for p in presets.values()],
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(part, path)

    @api.get("/presets")
    def list_presets() -> list[Preset]:
        return sorted(load().values(), key=lambda p: p.name.lower())

    @api.put("/presets/{name}")
    def save_preset(name: str, body: PresetBody) -> Preset:
        """Creates the preset, or replaces the one with this name."""
        name = name.strip()
        if not name or len(name) > 80:
            raise HTTPException(400, "Tên preset trống hoặc quá dài")
        presets = load()
        presets[name] = Preset(name=name, **body.model_dump())
        store(presets)
        return presets[name]

    @api.delete("/presets/{name}", status_code=204)
    def delete_preset(name: str):
        presets = load()
        if presets.pop(name, None) is None:
            raise HTTPException(404, "Không có preset này")
        store(presets)

    return api
