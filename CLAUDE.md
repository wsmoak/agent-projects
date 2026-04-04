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

Once you have built and deployed, in order to test the behavior, add a comment to this GitHub issue: https://github.com/wsmoak/rails-otel-demo/issues/94

Look at the most recent comment and do the same thing -- get the date and time and tag @openswe telling it to add a line to the end of the README.md file.

## Other

Avoid $() command substitution if possible.  For example use Bash to get todays date and time and then construct a string if you need it to comment on a github issue.
