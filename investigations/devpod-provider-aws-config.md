# Problem with Config for DevPod Provider AWS in Fargate

It's devpod-provider-aws. Specifically, the init.go command calls aws.NewAWSConfig() which calls awsConfig.LoadDefaultConfig(). Even when static credentials are provided via options, LoadDefaultConfig still tries to resolve the [default] shared config profile from ~/.aws/config. On Fargate (and other container environments), that file doesn't exist.

The fix on their side would be straightforward — something like adding awsConfig.WithSharedConfigFiles([]string{}) to the config options in buildConfigOptions when running in a containerized environment, or just handling the missing file gracefully.

Worth noting: the old loft-sh provider didn't have an init command that called the AWS API at all — that's new in skevetter's fork. So this is a regression specific to containerized/headless use cases.