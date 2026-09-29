<!-- acr:begin id=53c82e95ad403023cb4a2431e8247daf012fcac9525f0b99773dbc50f3e407b9 source=github:shelajev/coding-policy artifact=package-rules-1eb42d31b0d896b82a0b28e29cd3690e413ad988ee9985705cb284fbf6d62980 adapter=claude-code prefix=none -->
## ACR package: github:shelajev/coding-policy

### Rule: boundaries

# Respect the environment

Run application code and dependency containers inside the sandbox.
Never put real credentials in source, prompts, commits or result notes.
If an endpoint, file or tool is denied, report the operation and error to the human.
Do not change host policy, push commits or merge branches unless asked.

### Rule: server-validation

# Validate at the API boundary

Browser validation improves the UI; it does not protect the API.
Validate required input on the server and test the endpoint directly.
For the incident board, whitespace-only resolution notes must be rejected by the API.
Use parameterized SQL and escape user-supplied text rendered as HTML.

### Rule: small-changes

# Make the requested change

Read the task before editing. Implement the smallest change that meets its criteria.
Keep unrelated refactors and new dependencies out of the patch.
Only the developer edits application code; reviewers report findings.
When a requirement has two reasonable meanings, ask the human instead of guessing.

### Rule: tests-and-evidence

# Test behavior and report what happened

Add or update a test for the behavior being changed, including one relevant failure case.
Run the affected tests. Report the command and its actual exit code.
Never delete, skip or weaken a check just to make it pass.
Name the commit you implemented or reviewed. Distinguish a passing check from an
untested claim. A result note is a report; it is not an external approval.
<!-- acr:end id=53c82e95ad403023cb4a2431e8247daf012fcac9525f0b99773dbc50f3e407b9 -->
