# Public Import Scope

This branch contains a clean, reviewed export of Daily Business Agent source code, synthetic tests, public documentation, and Windows build scripts.

The import does not include private Git history, production configuration, credentials, certificates, certificate fingerprints, customer data, fixed-egress server deployment files, private repository references, or published release binaries.

All source, tests, and documentation use public-safe defaults and synthetic data. Runtime Lingxing credentials and TLS endpoint details must be supplied by the user and remain on the Windows computer.

The CI-generated Windows installer is a temporary review candidate. It is not a GitHub Release and must not be treated as a stable public binary until manual Windows acceptance is complete.
