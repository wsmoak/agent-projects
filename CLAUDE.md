# OpenSWE + DevPod

## Orientation

Read /Users/wsmoak/Projects/aws-infrastructure/open-swe/RUNBOOK.md

Work in the following repositories
1. this one (planning / orchestrating)
2. /Users/wsmoak/Projects/aws-infrastructure/open-swe (Terraform)
3. /Users/wsmoak/Projects/open-swe-aws-devpod-aegra (fork of OpenSWE)
4. /Users/wsmoak/Projects/wsmoak.github.io (blog)

If you need to look at the source code of DevPod, we have a fork at /Users/wsmoak/Projects/devpod .

If you need to look at the source code of Aegra, we have a fork at /Users/wsmoak/Projects/aegra .

The source code of devpod-provider-aws is in /Users/wsmoak/Projects/devpod-provider-aws

### Multi-Repo
1. The first project we used for testing is /Users/wsmoak/Projects/rails-otel-demo
2. Now we are adding /Users/wsmoak/Projects/django-polls-playwright-demo also with a devcontainer
3. AND we will have a multi-dev-container setup in /Users/wsmoak/Projects/multi-repo-dev-containers

## Notes

use `git -C` in order to execute git commands in a different directory.

Do not use `--profile terraform-admin` with aws commands.  While it works fine with `terraform apply` it does not work with other commands, perhaps due to a special character in the password.  You have the AWS environment variabls set, commands should just work.  If not, as the human to set the environment variables.

## Investigations

If you do an investigation to figure out how some code works or why something happened, write a markdown file in here in the investigations directory with the details.  Do this right after your investigation, do not wait until the end of the session.

## Troubleshooting

If you get this error "An error occurred (InvalidSignatureException) when calling the DescribeServices operation: The request signature we calculated does not match the signature you provided. Check your AWS Secret Access Key and signing method. Consult the service documentation for details." then ask the human to set the environment variables (and stop using `--profile terraform-admin`.)

## Subagents

You have subagents for infrastructure work:  `builder`, `watcher`, `deployer`, etc.  Use them.  Do not run the commands in Bash yourself.  Those commands produce A LOT of output that fills up your context.

## Testing

Once you have built and deployed, in order to test the behavior, create a new GitHub issue in https://github.com/wsmoak/rails-otel-demo and tag @openswe and tell it to do something simple like add the current timestamp to the end of the README file.

Look at a recent issue for an example.  Use bash to get the date in a separate command do not use && compound commands.

## Other

Avoid $() command substitution if possible.  For example use Bash to get todays date and time and then construct a string if you need it to comment on a github issue.
