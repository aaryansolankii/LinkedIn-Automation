# Deploy the hosted connector to Google Cloud Run

This runs `linkedin-mcp serve-http` on Cloud Run. The pieces:

- **Firestore** stores the calendar and your LinkedIn login.
- **Cloud Storage** stores the title cards.
- **Secret Manager** holds the secrets.
- **Cloud Scheduler** publishes approved posts every 15 minutes.

Run everything in **[Cloud Shell](https://shell.cloud.google.com)**, which already has `gcloud`, bash and Python. Start from a clone of this repo:

```bash
git clone https://github.com/aaryansolankii/LinkedIn-Automation.git
cd LinkedIn-Automation
git checkout hosted-mode   # until this branch is merged
```

You need:
- a Google Cloud project with billing enabled;
- your LinkedIn developer app's Client ID and Client Secret (see README, setup step 2).

## 1. Settings

```bash
PROJECT_ID="your-project-id"
REGION="asia-south1"                # Mumbai; any Cloud Run region works
SERVICE="linkedin-mcp"
BUCKET="${PROJECT_ID}-linkedin-cards"
TZ_NAME="Asia/Kolkata"              # the time zone your posting times are in
LINKEDIN_CLIENT_ID="your-linkedin-client-id"
SA="linkedin-mcp@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT_ID"
```

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

## 4. Bucket for title cards (private)

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

## 6. Secrets

Two random secrets are generated without being printed. The LinkedIn client secret is read without echoing it.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" \
  | gcloud secrets create mcp-path-secret --data-file=-
python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" \
  | gcloud secrets create publish-due-secret --data-file=-

read -rsp "LinkedIn client secret: " LINKEDIN_CLIENT_SECRET; echo
printf '%s' "$LINKEDIN_CLIENT_SECRET" | gcloud secrets create linkedin-client-secret --data-file=-
unset LINKEDIN_CLIENT_SECRET

for secret in mcp-path-secret publish-due-secret linkedin-client-secret; do
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
done
```

## 7. Keep the secret URL out of the logs

The app never logs request paths. Cloud Run's own request log does record every URL, and the connector URL contains the secret, so exclude that log for this service:

```bash
gcloud logging sinks update _Default \
  --add-exclusion="name=${SERVICE}-request-urls,filter=resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND log_id(\"run.googleapis.com/requests\")"
```

Application logs (`stderr`) are still kept.

## 8. Deploy to Cloud Run

Source deploys build the `Dockerfile` with Cloud Build. In newer projects the build runs as the Compute Engine default service account, which needs this role:

```bash
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/run.builder" --condition=None
```

Then deploy:

```bash
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --service-account "$SA" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 1 \
  --memory 512Mi \
  --set-env-vars "STORAGE=firestore,IMAGES_BUCKET=$BUCKET,TZ=$TZ_NAME,POSTING_TIME=10:00,LINKEDIN_CLIENT_ID=$LINKEDIN_CLIENT_ID" \
  --set-secrets "MCP_PATH_SECRET=mcp-path-secret:latest,PUBLISH_DUE_SECRET=publish-due-secret:latest,LINKEDIN_CLIENT_SECRET=linkedin-client-secret:latest"
```

`--allow-unauthenticated` is required because claude.ai and LinkedIn's login redirect call the service directly. Access is controlled by the secret path and the publish-due header instead: every other URL returns 404.

## 9. LinkedIn redirect URL

```bash
URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-env-vars "LINKEDIN_REDIRECT_URI=$URL/callback"
echo "Add this redirect URL in your LinkedIn app (Auth tab): $URL/callback"
```

To post as a **company page**, also set the author and organization scopes. The `^|^` prefix makes `|` the separator, because the scopes contain spaces:

```bash
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-env-vars "^|^LINKEDIN_AUTHOR_URN=urn:li:organization:123456|LINKEDIN_SCOPES=openid profile w_member_social w_organization_social r_organization_social"
```

## 10. Cloud Scheduler: publish due posts every 15 minutes

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

## 11. $5 budget alert

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

## 12. Your connector URL

```bash
echo "$URL/mcp/$(gcloud secrets versions access latest --secret=mcp-path-secret)"
```

This URL is the only thing protecting the connector, so treat it like a password. Then add it to claude.ai as described in the README, under "Hosted mode".

## Updating, rotating, removing

**Redeploy after code changes:** run step 8's `gcloud run deploy` again. Env vars and secrets are kept.

**Rotate the connector secret** (if the URL leaked):
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" \
  | gcloud secrets versions add mcp-path-secret --data-file=-
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-secrets "MCP_PATH_SECRET=mcp-path-secret:latest"
```
Then update the connector URL in claude.ai (step 12). Rotate `publish-due-secret` the same way, and recreate the scheduler job with the new header.

**Remove everything:**
```bash
gcloud scheduler jobs delete linkedin-publish-due --location "$REGION"
gcloud run services delete "$SERVICE" --region "$REGION"
gcloud storage rm --recursive "gs://$BUCKET"
gcloud secrets delete mcp-path-secret
gcloud secrets delete publish-due-secret
gcloud secrets delete linkedin-client-secret
```
Firestore data stays until you delete the database in the console.

## Troubleshooting

- **`--allow-unauthenticated` fails with an organization policy error:** the organization blocks public services (`iam.allowedPolicyMemberDomains`). Use a project outside that organization, or ask its admin for an exception.
- **The build fails with a permission error:** check step 8's `roles/run.builder` grant.
- **Posts go out at the wrong time:** `TZ` must be the zone your posting times are written in. Check that `linkedin_status` reports the expected `now`.
- **Logs:** go to Cloud Run → `linkedin-mcp` → Logs. Secrets and request paths are never written there by the app.
