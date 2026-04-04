# Issue: Provider init fails on AWS Fargate with "failed to get shared config profile, default"

## Title

`init` fails on Fargate/ECS with "failed to get shared config profile, default"

## Body

### Description

When running `devpod provider add aws` inside an AWS Fargate container, the provider's `init` command fails with:

```
error failed to get shared config profile, default
fatal configure provider: init: exit status 1
```

This happens even when AWS credentials are provided via `-o AWS_ACCESS_KEY_ID=... -o AWS_SECRET_ACCESS_KEY=... -o AWS_SESSION_TOKEN=...` and/or set as environment variables.

### Environment

- DevPod v0.18.2 (skevetter fork)
- devpod-provider-aws v0.4.0 (skevetter fork)
- Running on AWS Fargate (ECS) with task IAM role credentials
- No `~/.aws/config` or `~/.aws/credentials` files present

### Root Cause

The `init` command in `cmd/init.go` calls `aws.NewAWSConfig()`, which calls `awsConfig.LoadDefaultConfig()` in `pkg/aws/aws.go`.

The `buildConfigOptions` function correctly sets up a `StaticCredentialsProvider` when `AccessKeyID` and `SecretAccessKey` are provided. However, `LoadDefaultConfig` still attempts to resolve the `[default]` shared config profile from `~/.aws/config`. On Fargate (and other container-only environments), this file does not exist, causing the init to fail.

The relevant code path:

```go
// pkg/aws/aws.go
func buildConfigOptions(...) ([]func(*awsConfig.LoadOptions) error, error) {
    var opts []func(*awsConfig.LoadOptions) error
    // ... region and credential options added ...
    // But no option to disable/skip shared config profile loading
    return opts, nil
}

func NewAWSConfig(...) (aws.Config, error) {
    opts, err := buildConfigOptions(ctx, log, options)
    cfg, err := awsConfig.LoadDefaultConfig(ctx, opts...)  // fails here
}
```

### Workaround

Creating a minimal `~/.aws/config` before running `devpod provider add` resolves the issue:

```
[default]
region = us-east-2
```

### Suggested Fix

Add an option to `buildConfigOptions` that prevents `LoadDefaultConfig` from requiring the shared config file when static credentials are already provided. For example:

```go
// When static credentials are provided, don't require shared config
if options.AccessKeyID != "" && options.SecretAccessKey != "" {
    opts = append(opts, awsConfig.WithSharedConfigFiles([]string{}))
    opts = append(opts, awsConfig.WithSharedCredentialsFiles([]string{}))
}
```

Alternatively, this could be handled more broadly so the provider works in any container environment where `~/.aws/config` doesn't exist (Docker, Kubernetes, Fargate, etc.).

### Context

The previous loft-sh version of devpod-provider-aws did not have an `init` command that called the AWS API, so this is a new issue introduced with the skevetter fork's init behavior. The init step is useful (it validates credentials and sets default AMI), but it should gracefully handle environments without shared config files.
