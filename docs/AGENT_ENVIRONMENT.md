# Agent environment manifest

Run `basaltw agent manifest` in a project checkout for a concise discovery
summary, or `basaltw agent manifest /path/to/repo --json` for structured output.
It reports executables on the active session PATH, the current branch/commit
and dirty state, managed workspace conventions, artifact directories, and
declared branch-to-deployment mappings. It performs no installation, cloud
request, deployment, or authentication-file inspection. Tool availability does
not establish health; use the reported doctor command for that separate check.

Projects can commit an optional `basaltwater-agent.json` at their Git root.
This discovery file is separate from the `basaltwater.json` deployment manifest
and does not change CI or deployment behavior. For example:

```json
{
  "version": 1,
  "artifact_directories": [".artifacts", "dist"],
  "deployments": {
    "dev": {
      "environment": "development",
      "provider": "aws",
      "region": "us-east-1",
      "url": "https://dev.example.com/"
    },
    "staging": {
      "environment": "preproduction",
      "provider": "aws",
      "region": "us-east-1",
      "url": "https://staging.example.com/"
    }
  }
}
```

Use the project's verified mappings rather than assuming what a branch name
means. Each exact branch key requires `environment`; `provider`, `region`, and
`url` are optional. URLs use HTTPS without credentials, query strings, or
fragments. Omit credentials and environment-variable values. Unknown fields,
invalid Git branch names, malformed JSON, unsupported versions, symlinked or
non-regular files, and files larger than 64 KiB fail the command.

Artifact directories are relative to the repository and cannot escape it.
The summary reports whether Git ignores each declared directory; it does not
edit `.gitignore`. Tools missing from PATH appear as `null` in JSON, even if
another shell or Node selector could activate them. The command does not run
package-manager shims to discover versions because those can download tools.

`project_source` identifies the declaration used; `current_deployment` is the
mapping for the current branch, or `null` when undeclared or detached. Missing
`dev` and `staging` mappings are explicitly reported as unknown. The command
does not infer destinations from CI scripts, AWS credentials, or branch names.
Keep the declaration updated alongside changes to the project's deployment
configuration. Builds, tests, Git, and native rendering retain their existing
toolchains and commands.
