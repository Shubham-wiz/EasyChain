"""Single sign-on with OpenID Connect (Phase 5.6e, server/sso.py), against the test identity
provider (easychain.testing.oidc_provider): people sign in through it, are matched to their
account by their provider id or their email, or get a new account with the provider's role;
everything that should be refused is (another browser's sign-in, a bad or expired token, the
wrong audience or issuer, an unverified email, an email domain that isn't allowed); Owners set
it up, and the client secret goes to the vault."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from easychain.testing.oidc_provider import TestProvider

from .test_auth import _app, add_person, setup_owner, sign_in


@pytest.fixture(scope="module")
def idp() -> Iterator[TestProvider]:
    with TestProvider(client_id="easychain", client_secret="s3cret") as provider:
        yield provider


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, idp: TestProvider
) -> Iterator[TestClient]:
    monkeypatch.setenv("EASYCHAIN_AUTH", "on")
    idp.misbehave = None
    with TestClient(_app(tmp_path)) as test_client:
        setup_owner(test_client)
        saved = test_client.put(
            "/api/settings/sso",
            json={
                "providers": [
                    {
                        "id": "corp",
                        "name": "Corp Login",
                        "issuer": idp.issuer,
                        "client_id": "easychain",
                        "client_secret": "s3cret",
                        "new_people": "runner",
                        "domains": ["example.com"],
                    }
                ]
            },
        )
        assert saved.status_code == 200, saved.text
        test_client.cookies.clear()
        yield test_client


def sso(client: TestClient, email: str, next_page: str | None = None) -> httpx.Response:
    """Sign in through the provider as ``email``; the response is Easy Chain's last redirect."""
    params = {"next": next_page} if next_page else {}
    start = client.get("/api/auth/sso/corp", params=params, follow_redirects=False)
    assert start.status_code == 303, start.text
    there = start.headers["location"]
    assert "/authorize?" in there, there
    back = httpx.get(there + "&login_hint=" + email, follow_redirects=False)
    assert back.status_code == 302, back.text
    callback = urlsplit(back.headers["location"])
    return client.get(f"{callback.path}?{callback.query}", follow_redirects=False)


def error_of(response: httpx.Response) -> str:
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.path == "/", response.headers["location"]
    return parse_qs(location.query)["sso_error"][0]


# ── signing in ───────────────────────────────────────────────────────────────


def test_the_sign_in_page_offers_it(client: TestClient):
    assert client.get("/api/auth/status").json()["sso"] == [{"id": "corp", "name": "Corp Login"}]


def test_someone_with_an_account_signs_in_and_stays_linked(client: TestClient, idp: TestProvider):
    assert sign_in(client, "owner@example.com") == 200
    ed = add_person(client, "ed@example.com", "editor")
    client.cookies.clear()

    done = sso(client, "Ed@Example.com", next_page="/#/inbox")
    assert done.status_code == 303 and done.headers["location"] == "/#/inbox"
    assert "easychain_session=" in done.headers["set-cookie"]
    me = client.get("/api/auth/me").json()
    assert (me["id"], me["role"], me["via"]) == (ed["id"], "editor", "session")

    # Linked by the provider's id: a later sign-in finds them even with a new email there.
    client.cookies.clear()
    idp.users["ed.new@example.com"] = {"sub": idp.person("ed@example.com")["sub"]}
    assert sso(client, "ed.new@example.com").headers["location"] == "/"
    assert client.get("/api/auth/me").json()["id"] == ed["id"]
    idp.users.clear()

    # The start of the PKCE flow: S256, a state, a nonce.
    client.cookies.clear()
    start = client.get("/api/auth/sso/corp", follow_redirects=False)
    query = parse_qs(urlsplit(start.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"] and query["state"] and query["nonce"]
    assert query["redirect_uri"] == ["http://testserver/api/auth/sso/corp/callback"]
    assert (
        "HttpOnly" in start.headers["set-cookie"]
        and "Path=/api/auth/sso" in start.headers["set-cookie"]
    )


def test_new_people_get_the_providers_role_if_their_domain_is_allowed(client: TestClient):
    done = sso(client, "newbie@example.com")
    assert done.headers["location"] == "/"
    me = client.get("/api/auth/me").json()
    assert (me["email"], me["role"], me["name"]) == ("newbie@example.com", "runner", "Newbie")
    # No password: they can only sign in through the provider.
    assert sign_in(client, "newbie@example.com", "anything at all") == 401

    client.cookies.clear()
    message = error_of(sso(client, "someone@elsewhere.org"))
    assert message == "Single sign-on with Corp Login is only for @example.com addresses."


def test_only_people_an_owner_added_when_new_people_get_none(client: TestClient, idp: TestProvider):
    assert sign_in(client, "owner@example.com") == 200
    settings = client.get("/api/settings/sso").json()["providers"][0]
    client.put("/api/settings/sso", json={"providers": [{**settings, "new_people": "none"}]})
    client.cookies.clear()
    message = error_of(sso(client, "stranger@example.com"))
    assert (
        message
        == "There's no Easy Chain account for stranger@example.com. Ask an Owner to add you."
    )


@pytest.mark.parametrize(
    ("trouble", "says"),
    [
        ("wrong_nonce", "doesn't belong to this attempt"),
        ("wrong_audience", "meant for another app"),
        ("wrong_issuer", "issuer is wrong"),
        ("expired", "has expired"),
        ("wrong_key", "signature that doesn't check out"),
        ("unverified_email", "hasn't checked the email"),
        ("deny", "didn't sign you in (access_denied)"),
    ],
)
def test_what_should_be_refused_is(client: TestClient, idp: TestProvider, trouble: str, says: str):
    idp.misbehave = trouble
    message = error_of(sso(client, "ann@example.com"))
    assert says in message, message
    assert client.get("/api/auth/me").status_code == 401
    idp.misbehave = None


def test_a_sign_in_from_another_browser_is_refused(client: TestClient):
    """Login CSRF: someone sends you the callback of a sign-in they started."""
    start = client.get("/api/auth/sso/corp", follow_redirects=False)
    back = httpx.get(
        start.headers["location"] + "&login_hint=mallory@example.com", follow_redirects=False
    )
    callback = urlsplit(back.headers["location"])
    client.cookies.clear()  # the victim's browser has no such sign-in in progress
    message = error_of(client.get(f"{callback.path}?{callback.query}", follow_redirects=False))
    assert "didn't start in this browser" in message
    # And a state can't be used twice.
    assert error_of(client.get(f"{callback.path}?{callback.query}", follow_redirects=False))


def test_unfinished_sign_ins_are_forgotten(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    db = client.app.state.hub.db
    client.get("/api/auth/sso/corp", follow_redirects=False)
    assert len(client.portal.call(db.settings_starting, "sso_pending:")) == 1
    later = time.time() + 11 * 60
    monkeypatch.setattr(time, "time", lambda: later)
    client.get("/api/auth/sso/corp", follow_redirects=False)
    assert len(client.portal.call(db.settings_starting, "sso_pending:")) == 1, "only the new one"


def test_a_switched_off_account_cant_sign_in_this_way_either(client: TestClient):
    assert sign_in(client, "owner@example.com") == 200
    ed = add_person(client, "ed@example.com", "editor")
    client.patch(f"/api/people/{ed['id']}", json={"disabled": True})
    client.cookies.clear()
    assert "switched off" in error_of(sso(client, "ed@example.com"))


def test_the_wrong_client_secret_is_refused_by_the_provider(client: TestClient):
    assert sign_in(client, "owner@example.com") == 200
    client.put("/api/secrets/SSO_CORP_CLIENT_SECRET", json={"value": "not-it"})
    client.cookies.clear()
    assert "refused the sign-in code (invalid_client)" in error_of(sso(client, "ann@example.com"))


# ── setting it up ────────────────────────────────────────────────────────────


def test_owners_set_it_up_and_the_secret_goes_to_the_vault(client: TestClient):
    assert sign_in(client, "owner@example.com") == 200
    shown = client.get("/api/settings/sso").json()
    [corp] = shown["providers"]
    assert corp["client_secret"] == "{secret:SSO_CORP_CLIENT_SECRET}"
    assert corp["callback"] == "http://testserver/api/auth/sso/corp/callback"
    assert shown["source"] == "settings"
    assert {"name": "SSO_CORP_CLIENT_SECRET", "source": "vault"} in client.get(
        "/api/secrets"
    ).json()
    assert "s3cret" not in json.dumps(client.get("/api/settings/sso").json())
    # Sending it back as shown keeps it.
    assert client.put("/api/settings/sso", json={"providers": [corp]}).status_code == 200
    assert (
        client.get("/api/settings/sso").json()["providers"][0]["client_secret"]
        == corp["client_secret"]
    )

    for bad, says in [
        ({**corp, "issuer": "http://idp.example.com"}, "should be an https:// address"),
        ({**corp, "id": "Has Spaces"}, "lowercase letters"),
        ({**corp, "client_id": ""}, "needs its client id"),
        ({**corp, "new_people": "admin"}, "New people get a role"),
    ]:
        refused = client.put("/api/settings/sso", json={"providers": [bad]})
        assert refused.status_code == 422 and says in refused.json()["message"]
    entries = client.get("/api/audit", params={"kind": "settings.sso"}).json()["entries"]
    assert entries and "s3cret" not in json.dumps(entries)


def test_only_owners_see_or_change_it(client: TestClient):
    assert sign_in(client, "owner@example.com") == 200
    add_person(client, "ed@example.com", "editor")
    assert sign_in(client, "ed@example.com") == 200
    assert client.get("/api/settings/sso").status_code == 403
    assert client.put("/api/settings/sso", json={"providers": []}).status_code == 403


def test_signing_in_this_way_is_in_the_audit_log(client: TestClient, idp: TestProvider):
    sso(client, "newbie@example.com")
    idp.misbehave = "expired"
    client.cookies.clear()
    sso(client, "newbie@example.com")
    idp.misbehave = None
    assert sign_in(client, "owner@example.com") == 200
    log = client.get("/api/audit").json()["entries"]
    mine = [(e["actor"], e["action"], e["target"]) for e in reversed(log)]
    assert ("Newbie", "person.add", "newbie@example.com") in mine
    assert ("Newbie", "auth.sign_in", "newbie@example.com") in mine
    failed = next(e for e in log if e["action"] == "auth.sso_failed")
    assert "expired" in failed["detail"]["reason"] and failed["detail"]["provider"] == "corp"


def test_the_environment_can_set_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, idp: TestProvider
):
    monkeypatch.setenv("EASYCHAIN_AUTH", "on")
    monkeypatch.setenv("CORP_SECRET", "s3cret")
    monkeypatch.setenv(
        "EASYCHAIN_SSO",
        json.dumps(
            [
                {
                    "id": "corp",
                    "name": "Corp",
                    "issuer": idp.issuer,
                    "client_id": "easychain",
                    "client_secret": "{secret:CORP_SECRET}",
                    "new_people": "viewer",
                }
            ]
        ),
    )
    with TestClient(_app(tmp_path)) as client:
        setup_owner(client)
        assert client.get("/api/settings/sso").json()["source"] == "environment"
        assert client.put("/api/settings/sso", json={"providers": []}).status_code == 409
        client.cookies.clear()
        assert sso(client, "viv@anywhere.org").headers["location"] == "/"
        assert client.get("/api/auth/me").json()["role"] == "viewer"


def test_off_when_sign_in_is_off(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/api/auth/status").json()["sso"] == []
        assert client.get("/api/auth/sso/corp", follow_redirects=False).status_code == 404
