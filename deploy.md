# Deploy the hosted connector to Google Cloud Run

This runs `linkedin-mcp serve-http` on Cloud Run. The pieces:

- **Firestore** stores the calendar, your LinkedIn login and the connector's sign-ins (codes and tokens only as hashes).
- **Cloud Storage** stores post images.
- **Secret Manager** holds the secrets.
- **Cloud Scheduler** publishes approved posts every 15 minutes.
- **Sign in with Google** decides who may use the connector: only the addresses in `ALLOWED_EMAILS`. Anyone else, including other members of your Claude organization, gets "Access denied".

Already running the earlier version, whose connector URL ended in `/mcp/<secret>`? Read [Upgrading from the secret-URL version](#upgrading-from-the-secret-url-version) first.

Run everything in **[Cloud Shell](https://shell.cloud.google.com)**, which already has `gcloud`, bash and Python. Start from a clone of this repo:

```bash
git clone https://github.com/aaryansolankii/LinkedIn-Automation.git
cd LinkedIn-Automation
git checkout page-lock-and-images   # until this branch is merged
```

You need:
- a Google Cloud project with billing enabled;
- your LinkedIn developer app's Client ID and Client Secret (see README, setup step 2). To post as a company page, the app needs the *Community Management API* product.

## What's in the image

The `Dockerfile` builds a `python:3.13-slim` image with:
- `tzdata`, so `TZ` is honoured, and `fonts-dejavu-core` for `make_post_image`'s plain title cards;
- `assets/`: the Poppins and Inter fonts (SIL Open Font License, texts in `assets/fonts/`) and the transparent StatSigma logos used by `make_branded_image`;
- the Python dependencies from `uv.lock`, including `resvg-py`, which renders the branded images. It ships as a prebuilt wheel, so no browser or system graphics packages are needed, and rendering never touches the network.

## 1. Settings

```bash
PROJECT_ID="your-project-id"
REGION="asia-south1"                       # Mumbai; any Cloud Run region works
SERVICE="linkedin-mcp"
BUCKET="${PROJECT_ID}-linkedin-cards"
TZ_NAME="Asia/Kolkata"                     # the time zone your posting times are in
LINKEDIN_CLIENT_ID="your-linkedin-client-id"
LINKEDIN_PAGE_URN="urn:li:organization:123456"   # the company page to post as
ALLOWED_EMAILS="aaryan.solanki@thestatsigma.com" # who may use the connector (comma-separated)
SA="linkedin-mcp@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT_ID"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
URL="https://${SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"   # the service's URL, known before it exists
echo "$URL"
```

Cloud Run gives every service this `https://SERVICE-PROJECT_NUMBER.REGION.run.app` address. `$URL` is used for the Google and LinkedIn redirect URLs and for the connector, so all of them must use this same address.

## 2. Enable the APIs

```bash
gcloud services enable \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com storage.googleapis.com secretmanager.googleapis.com \
  cloudscheduler.googleapis.com logging.googleapis.com \
  cloudbilling.googleapis.com billingbudgets.googleapis.com
```

## 3. Firestore database

```bash
gcloud firestore databases create --location="$REGION" --type=firestore-native
```

Optional: let Firestore delete expired sign-in records by itself. The app ignores them either way.

```bash
for group in oauth_logins oauth_codes oauth_tokens; do
  gcloud firestore fields ttls update expire_at --collection-group="$group" --enable-ttl --async
done
```

## 4. Bucket for post images (private)

```bash
gcloud storage buckets create "gs://$BUCKET" \
  --location="$REGION" --uniform-bucket-level-access --public-access-prevention
```

## 5. Service account for the service

```bash
gcloud iam service-accounts create linkedin-mcp --display-name="LinkedIn MCP (Cloud Run)"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA" --role="roles/datastore.user" --condition=None
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA" --role="roles/storage.objectUser"
```

## 6. Google sign-in client

This is done in the Google Cloud console, in the same project. Google uses the client only to tell the service which email address signed in; the service asks for nothing else (scopes `openid email`) and doesn't keep Google's tokens.

1. Open **[Google Auth Platform](https://console.cloud.google.com/auth/overview)** (older consoles call it *APIs & Services → OAuth consent screen*) and click **Get started** if it asks.
2. **Branding:** app name `LinkedIn for Claude`, and your email as the support and developer contact email.
3. **Audience:**
   - If you can choose **Internal**, do. That option appears when the project belongs to a Google Workspace organization, such as `thestatsigma.com`. Then only accounts in that organization can even reach the sign-in.
   - Otherwise choose **External**, leave the publishing status at **Testing**, and under **Test users** add `aaryan.solanki@thestatsigma.com`. Only test users can sign in.
4. **Data Access:** nothing to add. `openid` and `email` are allowed by default and need no verification.
5. **Clients → Create client:**
   - Application type: **Web application**
   - Name: `LinkedIn MCP connector`
   - Authorized redirect URIs: `$URL/oauth/google/callback` (run `echo "$URL/oauth/google/callback"` and paste the result)
   - Leave *Authorized JavaScript origins* empty.
6. Click **Create** and copy the **Client ID** and **Client secret**. Newer consoles show the secret only now; if you lose it, add a new secret to the client.

```bash
GOOGLE_OAUTH_CLIENT_ID="1234567890-abc.apps.googleusercontent.com"
```

`ALLOWED_EMAILS` is the real gate: even an account that may sign in with Google is turned away unless its address is on that list.

## 7. Secrets

Two random secrets are generated without being printed. The LinkedIn and Google client secrets are read without echoing them.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" \
  | gcloud secrets create publish-due-secret --data-file=-
python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" \
  | gcloud secrets create oauth-signing-key --data-file=-

read -rsp "LinkedIn client secret: " LINKEDIN_CLIENT_SECRET; echo
printf '%s' "$LINKEDIN_CLIENT_SECRET" | gcloud secrets create linkedin-client-secret --data-file=-
unset LINKEDIN_CLIENT_SECRET

read -rsp "Google OAuth client secret: " GOOGLE_OAUTH_CLIENT_SECRET; echo
printf '%s' "$GOOGLE_OAUTH_CLIENT_SECRET" | gcloud secrets create google-oauth-client-secret --data-file=-
unset GOOGLE_OAUTH_CLIENT_SECRET

for secret in publish-due-secret oauth-signing-key linkedin-client-secret google-oauth-client-secret; do
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
done
```

- `publish-due-secret` lets Cloud Scheduler call `/internal/publish-due`.
- `oauth-signing-key` signs LinkedIn login links and derives the secrets of registered connector clients, so those are never stored. Changing it signs Claude out of the connector.

## 8. Keep request URLs out of the logs

The app never logs secrets, codes or tokens. Cloud Run's own request log does record every URL, and the sign-in redirects carry one-time codes in theirs, so exclude that log for this service:

```bash
gcloud logging sinks update _Default \
  --add-exclusion="name=${SERVICE}-request-urls,filter=resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND log_id(\"run.googleapis.com/requests\")"
```

Application logs (`stderr`) are still kept.

## 9. Deploy to Cloud Run

Source deploys build the `Dockerfile` with Cloud Build. In newer projects the build runs as the Compute Engine default service account, which needs this role:

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/run.builder" --condition=None
```

Then deploy. The `^|^` prefix makes `|` the separator between variables, because `ALLOWED_EMAILS` contains commas and `LINKEDIN_SCOPES` contains spaces:

```bash
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --service-account "$SA" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 1 \
  --memory 512Mi \
  --set-env-vars "^|^STORAGE=firestore|IMAGES_BUCKET=$BUCKET|TZ=$TZ_NAME|POSTING_TIME=10:00|LINKEDIN_CLIENT_ID=$LINKEDIN_CLIENT_ID|LINKEDIN_REDIRECT_URI=$URL/callback|LINKEDIN_AUTHOR_URN=$LINKEDIN_PAGE_URN|LINKEDIN_SCOPES=w_organization_social r_organization_social|PAGE_ONLY=true|OAUTH_ISSUER_URL=$URL|GOOGLE_OAUTH_CLIENT_ID=$GOOGLE_OAUTH_CLIENT_ID|ALLOWED_EMAILS=$ALLOWED_EMAILS" \
  --set-secrets "PUBLISH_DUE_SECRET=publish-due-secret:latest,OAUTH_SIGNING_KEY=oauth-signing-key:latest,LINKEDIN_CLIENT_SECRET=linkedin-client-secret:latest,GOOGLE_OAUTH_CLIENT_SECRET=google-oauth-client-secret:latest"
```

| Setting | Meaning |
|---|---|
| `OAUTH_ISSUER_URL` | The service URL (`$URL`, no trailing slash). The connector lives at `$URL/mcp`. |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | The Google client from step 6. |
| `ALLOWED_EMAILS` | Comma-separated addresses allowed to use the connector, case-insensitive. Removing one cuts its access at the next request. |
| `OAUTH_SIGNING_KEY` | Random key from step 7 (32+ characters, different from `PUBLISH_DUE_SECRET`). |
| `PAGE_ONLY=true` | Only ever post as a company page: publishing refuses any author that isn't `urn:li:organization:...`, and `linkedin_login` refuses scopes that include `w_member_social`. |

The service refuses to start if any of these is missing.

`--allow-unauthenticated` is required because claude.ai and the Google and LinkedIn sign-in redirects reach the service directly. Access is controlled by the service itself: `/mcp` needs a token that only a signed-in, allowed account can get, `/internal/publish-due` needs its secret header, and every other URL returns 404.

## 10. LinkedIn redirect URL

```bash
echo "Add this redirect URL in your LinkedIn app (Auth tab): $URL/callback"
```

`LINKEDIN_SCOPES` above only asks for company-page rights. `linkedin_status` then reports the connection from the saved token and its expiry, since the token can't read your profile.

## 11. Cloud Scheduler: publish due posts every 15 minutes

This only publishes posts you approved with `schedule_post` whose time has come. It never publishes drafts.

```bash
gcloud scheduler jobs create http linkedin-publish-due \
  --location "$REGION" \
  --schedule "*/15 * * * *" \
  --time-zone "$TZ_NAME" \
  --uri "$URL/internal/publish-due" \
  --http-method POST \
  --headers "X-Publish-Due-Secret=$(gcloud secrets versions access latest --secret=publish-due-secret)" \
  --attempt-deadline 120s
```

The header value is stored in the job's configuration. Anyone who can view Cloud Scheduler jobs in this project can read it.

## 12. $5 budget alert

```bash
BILLING_ACCOUNT="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)')"
gcloud billing budgets create \
  --billing-account "${BILLING_ACCOUNT#billingAccounts/}" \
  --display-name "linkedin-mcp" \
  --budget-amount 5USD \
  --filter-projects "projects/$PROJECT_ID" \
  --threshold-rule percent=0.5 \
  --threshold-rule percent=0.9 \
  --threshold-rule percent=1.0
```

Keep these limits in mind:
- The amount's currency must match your billing account's. For an INR account, use for example `--budget-amount 450INR`.
- A budget only sends alert emails, to billing admins. It does not cap spending.

## 13. Check it and add the connector

```bash
curl -s "$URL/.well-known/oauth-protected-resource"; echo   # "resource" must be exactly $URL/mcp
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$URL/mcp"  # 401: a token is required
echo "Connector URL: $URL/mcp"
```

Then add the connector in claude.ai as described in the README, under "Hosted mode". The URL isn't a secret any more: without signing in as an allowed account it only returns 401.

## Upgrading from the secret-URL version

The earlier version served MCP at `/mcp/<MCP_PATH_SECRET>`. That endpoint is gone; `/callback` (LinkedIn login) and `/internal/publish-due` work as before, and the scheduler job needs no change.

1. Run step 1, but take the URL your service already has, so the LinkedIn redirect URL you registered keeps working:

   ```bash
   URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
   ```

2. Create the Google sign-in client (step 6) with the redirect URI `$URL/oauth/google/callback`.
3. Create the two new secrets and grant access to them:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" \
     | gcloud secrets create oauth-signing-key --data-file=-
   read -rsp "Google OAuth client secret: " GOOGLE_OAUTH_CLIENT_SECRET; echo
   printf '%s' "$GOOGLE_OAUTH_CLIENT_SECRET" | gcloud secrets create google-oauth-client-secret --data-file=-
   unset GOOGLE_OAUTH_CLIENT_SECRET
   for secret in oauth-signing-key google-oauth-client-secret; do
     gcloud secrets add-iam-policy-binding "$secret" \
       --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
   done
   ```

4. Redeploy with the new settings, and drop the old secret from the service:

   ```bash
   gcloud run deploy "$SERVICE" --source . --region "$REGION" \
     --update-env-vars "^|^OAUTH_ISSUER_URL=$URL|GOOGLE_OAUTH_CLIENT_ID=$GOOGLE_OAUTH_CLIENT_ID|ALLOWED_EMAILS=$ALLOWED_EMAILS|PAGE_ONLY=true|LINKEDIN_AUTHOR_URN=$LINKEDIN_PAGE_URN|LINKEDIN_SCOPES=w_organization_social r_organization_social" \
     --update-secrets "OAUTH_SIGNING_KEY=oauth-signing-key:latest,GOOGLE_OAUTH_CLIENT_SECRET=google-oauth-client-secret:latest" \
     --remove-secrets MCP_PATH_SECRET
   ```

5. Delete the old secret: `gcloud secrets delete mcp-path-secret`.
6. Optionally add the Firestore TTL policies from step 3.
7. In claude.ai, remove the old connector (its URL contained the secret) and add the new one as described in the README.
8. If your saved LinkedIn login was made with `w_member_social`, ask Claude to run `linkedin_login` again so the stored token only carries company-page rights. Posting as a person is refused either way while `PAGE_ONLY=true`.

## Updating, rotating, removing

**Redeploy after code changes:** run step 9's `gcloud run deploy` again. Env vars and secrets are kept.

**Allow or remove an account:** change `ALLOWED_EMAILS`. A removed address loses access at its next request.

```bash
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "^|^ALLOWED_EMAILS=$ALLOWED_EMAILS"
```

**Rotate a secret:** add a new version, then point the service at it. For example:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" \
  | gcloud secrets versions add publish-due-secret --data-file=-
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-secrets "PUBLISH_DUE_SECRET=publish-due-secret:latest"
```

- `publish-due-secret`: also recreate the scheduler job with the new header (step 11).
- `oauth-signing-key`: signs Claude out of the connector. Remove the connector in claude.ai and add it again.
- `google-oauth-client-secret`: first add a new secret to the client in the console, then store it with `printf '%s' ... | gcloud secrets versions add ...` as in step 7.

**Remove everything:**
```bash
gcloud scheduler jobs delete linkedin-publish-due --location "$REGION"
gcloud run services delete "$SERVICE" --region "$REGION"
gcloud storage rm --recursive "gs://$BUCKET"
for secret in publish-due-secret oauth-signing-key linkedin-client-secret google-oauth-client-secret; do
  gcloud secrets delete "$secret"
done
```
Firestore data stays until you delete the database in the console. Delete the Google client under Google Auth Platform → Clients.

## Troubleshooting

- **"Access denied" after choosing a Google account:** that address isn't in `ALLOWED_EMAILS`. The service log names the address it refused.
- **Google shows `redirect_uri_mismatch`:** the client's authorized redirect URI must be exactly `$OAUTH_ISSUER_URL/oauth/google/callback`.
- **Google says the app is in testing or access is blocked:** add your address under Audience → Test users (External), or sign in with an account from the organization (Internal).
- **claude.ai can't connect or register:** the connector URL must be exactly `$OAUTH_ISSUER_URL/mcp`, on the same host as `OAUTH_ISSUER_URL`. The service only registers clients whose redirect URL is Claude's callback (`https://claude.ai/api/mcp/auth_callback` or `https://claude.com/api/mcp/auth_callback`) or on your own machine (`http://localhost`); the log names any it refused. If Claude ever moves its callback, add it to `CLAUDE_CALLBACKS` in `src/linkedin_mcp/oauth.py`.
- **`--allow-unauthenticated` fails with an organization policy error:** the organization blocks public services (`iam.allowedPolicyMemberDomains`). Use a project outside that organization, or ask its admin for an exception.
- **The build fails with a permission error:** check step 9's `roles/run.builder` grant.
- **The service doesn't start:** the log lists the missing settings, for example `Hosted mode needs these settings: OAUTH_ISSUER_URL`.
- **Posts go out at the wrong time:** `TZ` must be the zone your posting times are written in. Check that `linkedin_status` reports the expected `now`.
- **Logs:** go to Cloud Run → `linkedin-mcp` → Logs. Secrets, codes and tokens are never written there by the app.
