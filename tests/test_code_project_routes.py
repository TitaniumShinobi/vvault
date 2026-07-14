from vvault.server import vvault_web_server as server


def _auth_headers():
    return {"Authorization": "Bearer test-token"}


class FakeCodeProjectRepository:
    def __init__(self):
        self.projects = {}
        self.files = {}

    def list_projects(self, *, user_id):
        return list(self.projects.values())

    def get_project(self, *, user_id, project_instance_id):
        return self.projects.get(project_instance_id)

    def upsert_project(self, *, user_id, project):
        project_instance_id = project["projectInstanceId"]
        saved = {
            **project,
            "id": project_instance_id,
            "projectId": project_instance_id,
            "projectInstanceId": project_instance_id,
            "rootPath": f".hydro/workspaces/{project_instance_id}",
            "vvaultStoragePath": f"code/projects/{project_instance_id}/project.json",
        }
        self.projects[project_instance_id] = saved
        return saved

    def list_files(self, *, user_id, project_instance_id):
        return [
            {"relativePath": relative_path, "storagePath": f"code/projects/{project_instance_id}/files/{relative_path}"}
            for relative_path in sorted(self.files.get(project_instance_id, {}))
        ]

    def read_file(self, *, user_id, project_instance_id, relative_path):
        content = self.files.get(project_instance_id, {}).get(relative_path)
        if content is None:
            return None
        return {"relativePath": relative_path, "content": content}

    def upsert_file(self, *, user_id, project_instance_id, relative_path, content, content_type="text/plain"):
        self.files.setdefault(project_instance_id, {})[relative_path] = content
        return {"relativePath": relative_path, "storagePath": f"code/projects/{project_instance_id}/files/{relative_path}"}

    def delete_file(self, *, user_id, project_instance_id, relative_path):
        return self.files.get(project_instance_id, {}).pop(relative_path, None) is not None

    def list_transcript_links(self, *, project_instance_id):
        return [{"id": "transcript-1", "title": f"{project_instance_id} Hydro thread"}]


def _patch_auth(monkeypatch):
    monkeypatch.setattr(
        server,
        "db_get_session",
        lambda token: {"email": "devon@example.com", "name": "Devon", "role": "user"},
    )
    monkeypatch.setattr(server, "_current_vvault_user_id", lambda: ("user-1", None))


def test_code_projects_endpoint_returns_signed_in_user_projects(monkeypatch):
    server.app.config["TESTING"] = True
    fake_repo = FakeCodeProjectRepository()
    fake_repo.upsert_project(user_id="user-1", project={"projectInstanceId": "stable-1", "name": "Stable"})
    _patch_auth(monkeypatch)
    monkeypatch.setattr(server, "_code_project_repository", lambda: fake_repo)

    response = server.app.test_client().get("/api/code/projects", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["storage_owner"] == "ovvaults.vault_files"
    assert payload["transcript_owner"] == "ovvaults.transcripts"
    assert payload["projects"][0]["projectInstanceId"] == "stable-1"
    assert payload["projects"][0]["rootPath"] == ".hydro/workspaces/stable-1"


def test_code_project_upsert_and_get_are_keyed_by_project_instance_id(monkeypatch):
    server.app.config["TESTING"] = True
    fake_repo = FakeCodeProjectRepository()
    _patch_auth(monkeypatch)
    monkeypatch.setattr(server, "_code_project_repository", lambda: fake_repo)
    client = server.app.test_client()

    first = client.post(
        "/api/code/projects",
        headers=_auth_headers(),
        json={"projectInstanceId": "same-id", "name": "Display A"},
    )
    second = client.post(
        "/api/code/projects",
        headers=_auth_headers(),
        json={"projectInstanceId": "same-id", "name": "Display B"},
    )
    fetched = client.get("/api/code/projects/same-id", headers=_auth_headers())

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(fake_repo.projects) == 1
    assert fetched.status_code == 200
    payload = fetched.get_json()
    assert payload["project"]["name"] == "Display B"
    assert payload["transcripts"][0]["id"] == "transcript-1"


def test_code_project_migration_imports_non_internal_files_once(monkeypatch):
    server.app.config["TESTING"] = True
    fake_repo = FakeCodeProjectRepository()
    _patch_auth(monkeypatch)
    monkeypatch.setattr(server, "_code_project_repository", lambda: fake_repo)

    response = server.app.test_client().post(
        "/api/code/projects/migrate",
        headers=_auth_headers(),
        json={
            "projects": [
                {
                    "projectInstanceId": "migrated-1",
                    "name": "Migrated",
                    "files": [
                        {"relativePath": "src/App.tsx", "content": "export const ok = true;"},
                        {"relativePath": "state.json", "content": "{}"},
                        {"relativePath": "node_modules/pkg/index.js", "content": "skip"},
                    ],
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["project_count"] == 1
    assert payload["file_count"] == 1
    assert fake_repo.files["migrated-1"] == {"src/App.tsx": "export const ok = true;"}
