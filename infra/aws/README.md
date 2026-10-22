# Public demo on S3 + CloudFront

The public demo is the UI as static files: no backend, no API keys, ~$0/month
(ADR 017). One CloudFormation stack, [demo.yml](demo.yml), holds all of it:

| Resource | Why |
|---|---|
| Private S3 bucket | The build output (`frontend/out/`). Public access is blocked; only the distribution can read it. |
| CloudFront distribution + origin access control | HTTPS at a `*.cloudfront.net` address, compression, HTTP/3, security headers. Missing paths, `/api/*` included, get the app's 404 page, which the UI reads as "live mode offline". |
| GitHub OIDC provider | Lets GitHub Actions sign in to AWS with a short-lived token instead of stored keys. |
| Deploy role | Assumable only by workflow runs on `main` of this repository. It can replace the bucket's files and invalidate this distribution, nothing else. |

## Create it

With AWS credentials for the account (us-east-1 keeps everything next to CloudFront's
own region):

```sh
aws cloudformation deploy --region us-east-1 --stack-name aidev-demo \
  --template-file infra/aws/demo.yml --capabilities CAPABILITY_IAM
aws cloudformation describe-stacks --region us-east-1 --stack-name aidev-demo \
  --query "Stacks[0].Outputs" --output table
```

If the account already has a GitHub OIDC provider, add
`--parameter-overrides CreateGitHubOidcProvider=false`.

Then, in the GitHub repo under Settings → Secrets and variables → Actions → Variables
(none of these are secrets):

| Variable | Value |
|---|---|
| `DEMO_ROLE_ARN` | output `DeployRoleArn` |
| `DEMO_BUCKET` | output `BucketName` |
| `DEMO_DISTRIBUTION_ID` | output `DistributionId` |
| `DEMO_URL` | output `SiteUrl`, used by the smoke test |
| `DEMO_REGION` | optional, defaults to `us-east-1` |

From then on, every merge to `main` that passes CI publishes the demo
([deploy.yml](../../.github/workflows/deploy.yml), job `demo`). Run the Deploy workflow
by hand for the first publish.

## Costs

CloudFront's always-free tier covers 1 TB and 10 million requests a month, and the
bucket holds ~1.5 MB. Invalidating `/*` once per deploy stays inside the 1,000 free
invalidation paths a month. The account's existing cost budgets alert on spending.

## Remove it

```sh
aws s3 rm s3://<BucketName> --recursive
aws cloudformation delete-stack --region us-east-1 --stack-name aidev-demo
aws s3 rb s3://<BucketName>   # the bucket is retained by the stack, so delete it last
```

Then delete the `DEMO_*` variables, so merges skip the job instead of failing.
