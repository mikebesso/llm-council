from fastapi.testclient import TestClient
from backend.main import app




def test_council_smoke():
    client = TestClient(app)

    # create a conversation to get an id
    convo = client.post("/api/conversations", json={}).json()
    assert "id" in convo
    cid = convo["id"]
    assert cid 

    r = client.post(f"/api/conversations/{cid}/message", json={"content": "Hello council"})
    assert r.status_code == 200
    body = r.json()
    assert "stage1" in body # non-empty
    assert "stage2"  in body # non-empty
    assert "stage3"  in body # non-empty