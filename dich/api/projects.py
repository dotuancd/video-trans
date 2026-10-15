# Projects: a name, a color and the default options for the videos added to them; jobs point at one.
from fastapi import APIRouter, HTTPException

from dich.api.models import Project, ProjectBody


def router(ctx):
    api = APIRouter()
    store = ctx.store

    def project_or_404(project_id):
        project = store.project(project_id)
        if not project:
            raise HTTPException(404, "Không có project này")
        return project

    def checked(body):
        fields = body.model_dump()
        fields["name"] = fields["name"].strip()
        if not fields["name"] or len(fields["name"]) > 80:
            raise HTTPException(400, "Tên project trống hoặc quá dài")
        export_dir = (fields["export_dir"] or "").strip()
        fields["export_dir"] = str(ctx.inside(export_dir)) if export_dir else None
        return fields

    @api.get("/projects")
    def list_projects() -> list[Project]:
        return store.projects()

    @api.post("/projects")
    def create_project(body: ProjectBody) -> Project:
        return store.project(store.create_project(checked(body)))

    @api.put("/projects/{project_id}")
    def save_project(project_id: str, body: ProjectBody) -> Project:
        project_or_404(project_id)
        store.update_project(project_id, checked(body))
        return store.project(project_id)

    @api.delete("/projects/{project_id}", status_code=204)
    def delete_project(project_id: str):
        """Its jobs stay, without a project."""
        project_or_404(project_id)
        store.delete_project(project_id)

    return api
