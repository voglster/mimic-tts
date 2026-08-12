import pytest
from mimic_server.db import Database
from mimic_server.errors import LabelInUse
from mimic_server.identity import KeyStore, generate_token, hash_token, prefix_of


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "mimic.db")
    db.migrate()
    return KeyStore(db)


def test_generated_tokens_are_prefixed_and_unique():
    a, b = generate_token(), generate_token()
    assert a.startswith("mk_")
    assert b.startswith("mk_")
    assert a != b
    assert prefix_of(a) == a[3:11]


def test_create_returns_key_and_plaintext_once(store):
    key, token = store.create("dave")
    assert key.label == "dave"
    assert key.role == "user"
    assert key.enabled is True
    assert key.can_upload is True
    assert key.max_voices == 5
    assert key.daily_char_quota == 50000
    assert key.token_prefix == prefix_of(token)


def test_stored_hash_is_not_the_token(store):
    _, token = store.create("dave")
    with store.db.cursor() as cur:
        cur.execute("SELECT token_hash FROM api_keys WHERE label = 'dave'")
        stored = cur.fetchone()["token_hash"]
    assert stored != token
    assert stored == hash_token(token)


def test_duplicate_label_rejected(store):
    store.create("dave")
    with pytest.raises(LabelInUse):
        store.create("dave")


def test_authenticate_round_trip(store):
    _, token = store.create("dave")
    assert store.authenticate(token).label == "dave"


def test_authenticate_rejects_unknown_disabled_and_expired(store):
    assert store.authenticate("mk_nope") is None

    _, token = store.create("dave")
    store.update("dave", enabled=False)
    assert store.authenticate(token) is None

    _, token2 = store.create("erin", expires_at="2000-01-01T00:00:00+00:00")
    assert store.authenticate(token2) is None


def test_authenticate_rejects_right_prefix_wrong_secret(store):
    _, token = store.create("dave")
    forged = token[:11] + ("x" * (len(token) - 11))
    assert store.authenticate(forged) is None


def test_update_changes_quotas(store):
    store.create("dave")
    updated = store.update("dave", max_voices=1, daily_char_quota=10, can_upload=False)
    assert (updated.max_voices, updated.daily_char_quota, updated.can_upload) == (1, 10, False)


def test_touch_sets_last_used(store):
    key, _ = store.create("dave")
    assert key.last_used_at is None
    store.touch(key.id)
    assert store.get_by_label("dave").last_used_at is not None


def test_ensure_env_root_is_idempotent_and_rotates(store):
    root = store.ensure_env_root("secret-one", "root")
    assert root.is_admin
    assert root.managed_by_env is True
    again = store.ensure_env_root("secret-two", "root")
    assert again.id == root.id
    assert store.authenticate("secret-one") is None
    assert store.authenticate("secret-two").id == root.id
