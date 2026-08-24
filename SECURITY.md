# Security and data handling

ASCEND is research software for processing radiotherapy DICOM data. DICOM files, ASCEND case directories, logs, exports, screenshots, crash reports, and derived arrays may contain protected health information or reconstructable patient data.

## Repository rules

- Do not commit clinical DICOM, case manifests, validated runs, caches, exports, logs, screenshots, crash reports, dose arrays, masks, or meshes.
- Use only explicitly synthetic, non-clinical fixtures in tests.
- Inspect staged content before every push with `git diff --cached` and `git status --short`.
- Treat removal from the latest commit as insufficient if sensitive data entered Git history. Purge the history and rotate any exposed credentials before publication.
- Keep the optional browser workstation bound to localhost unless a separate authenticated deployment and security review has been completed.

## Clinical scope

ASCEND is not approved clinical decision software. Layer 3.1 and Layer 3.2 outputs are research-model results and are not toxicity, outcome, TCP/NTCP, or treatment recommendations unless a result contract explicitly states otherwise.

## Reporting a vulnerability

Do not place patient data, credentials, private DICOM UIDs, or exploitable details in a public issue. Use the repository owner's private GitHub security-reporting channel.
