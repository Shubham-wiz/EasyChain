# Single sign-on

People can sign in with your company's identity provider instead of an Easy Chain password.
This works with any provider that speaks **OpenID Connect**, such as Okta, Google, Microsoft
Entra ID, Keycloak, Auth0 or Authentik. Passwords keep working alongside it.

Single sign-on needs [sign-in](people.md) to be on, and a first Owner, who sets it up. It
applies to every [workspace](workspaces.md), so only the server's Owners (Owners of the
default workspace) set it up. People who join through it join the default workspace.

## Setting it up

1. **Make an app at your provider.** It is usually called a "web application" or an "OIDC
   client". Give it this redirect address:

   ```
   https://<your Easy Chain address>/api/auth/sso/<id>/callback
   ```

   `<id>` is a short name you pick, such as `okta`. Easy Chain shows the exact address in
   Settings once the provider is saved. Set `EASYCHAIN_PUBLIC_URL` to the address people use,
   so the redirect address is right behind a proxy.
2. **Copy three things** from the provider: the **issuer** (its base address), the **client
   id** and the **client secret**.
3. In Easy Chain, go to **Settings → Single sign-on** and choose **Add a provider**. Fill in:
   - **Name on the sign-in page**, shown as "Sign in with …";
   - **Id**: the one in the redirect address;
   - **Issuer**, **Client id** and **Client secret**. The secret is kept in the encrypted
     secrets vault, never in settings;
   - **People signing in for the first time**:
     - *Can't*: an Owner adds them under Settings → People first; or
     - *Get an account as* a role, usually Viewer or Runner;
   - **Only these email domains**: optional, for example `example.com`.

   Then choose **Save**.
4. Sign out. The sign-in page now shows **Sign in with <name>**.

### Okta

In the Admin Console, go to **Applications → Create App Integration → OIDC → Web
Application**.

- **Sign-in redirect URI**: the address above.
- **Issuer**: `https://<your-org>.okta.com`, or the issuer of a custom authorization
  server, such as `https://<your-org>.okta.com/oauth2/default`.
- Assign the people or groups who may use Easy Chain.

### Google

In Google Cloud Console, go to **APIs & Services → Credentials → Create credentials → OAuth
client ID → Web application**.

- **Authorized redirect URI**: the address above.
- **Issuer**: `https://accounts.google.com`.
- Google lets any Google account sign in. Put your company's domain in **Only these email
  domains**.

### Microsoft Entra ID

In the Entra admin centre, go to **App registrations → New registration**.

- **Redirect URI**: platform *Web*, the address above.
- Under **Certificates & secrets**, make a client secret.
- **Issuer**: `https://login.microsoftonline.com/<tenant id>/v2.0`. The tenant id is on the
  app's *Overview* page.
- Under **Token configuration**, add the optional claim `email`, so Easy Chain gets people's
  email addresses.

## How Easy Chain matches people

A provider's first sign-in for someone is matched to an Easy Chain account in this order:

1. the account already linked to that person at this provider, by the provider's own id for
   them (so a changed email keeps working);
2. the account with the same email, which is then linked;
3. a new account with the role for new people, if that's allowed and their domain is.

Otherwise they're told to ask an Owner to add them. People made this way have no Easy Chain
password, so they can only sign in through the provider. A switched-off account can't sign in
this way either.

## What is checked

- The browser that comes back is the one that started. Easy Chain sets a short-lived cookie
  with a random `state`, which stops someone sending you a sign-in they started (login CSRF).
  A state works once, and for 10 minutes.
- **PKCE** (S256): the code is useless to anyone who doesn't also have the secret verifier.
- The **ID token's signature** is checked against the provider's published keys. Only
  public-key algorithms are accepted, never `none` or a shared secret.
- The ID token's **issuer**, **audience** (your client id), **expiry** (allowing a minute of
  clock difference) and **nonce**.
- The **email is verified** by the provider.

When something fails, the sign-in page says what went wrong. Failures and successful sign-ins
are in the [audit log](audit.md).

## Set it for the whole server

`EASYCHAIN_SSO` takes a JSON list of providers. It wins over Settings, which then shows them
read-only:

```bash
EASYCHAIN_SSO='[{"id": "okta", "name": "Okta", "issuer": "https://acme.okta.com",
  "client_id": "0oa…", "client_secret": "{secret:OKTA_CLIENT_SECRET}",
  "new_people": "viewer", "domains": ["acme.com"]}]'
OKTA_CLIENT_SECRET=…
```

`client_secret` names a secret, here `OKTA_CLIENT_SECRET`, which comes from the secrets vault
or the server's environment.

## Trying it locally

`easychain.testing.oidc_provider` is a small OpenID Connect provider for tests. It asks for an
email and signs that person in:

```bash
python -m easychain.testing.oidc_provider --port 8127 --client-id easychain --client-secret s3cret
```

Add it as a provider with the issuer `http://127.0.0.1:8127`. An `http://` issuer is accepted
only for `localhost` and `127.0.0.1`. The Playwright journeys and `tests/test_sso.py` use it.
