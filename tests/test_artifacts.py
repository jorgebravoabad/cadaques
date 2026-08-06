from __future__ import annotations

from cadaques.core.artifacts import ArtifactRef, store_artifact


def test_store_bytes_is_content_addressed_and_idempotent(tmp_path):
    a = store_artifact(b"hello campaign", tmp_path, media_type="text/plain", note="x")
    b = store_artifact(b"hello campaign", tmp_path)
    assert a.sha256 == b.sha256 and a.uri == b.uri
    assert a.size_bytes == len(b"hello campaign")
    assert (tmp_path / a.sha256[:2] / a.sha256).read_bytes() == b"hello campaign"


def test_store_file_and_lineage(tmp_path):
    src = tmp_path / "data.txt"
    src.write_text("payload")
    parent = store_artifact(b"raw", tmp_path)
    ref = store_artifact(src, tmp_path, parents=(parent.sha256,))
    assert ref.parents == (parent.sha256,)
    assert isinstance(ref, ArtifactRef) and ref.as_dict()["parents"] == [parent.sha256]
