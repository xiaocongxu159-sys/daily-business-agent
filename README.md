# Daily Business Agent

> Pre-release repository foundation. The application source code and installers have not been published yet.

Daily Business Agent is a planned local-first Windows tool for processing user-provided business report files and presenting local analysis results.

## Current status

This repository currently contains only the public project foundation:

- Apache License 2.0;
- security and contribution policies;
- safe ignore rules;
- project attribution information.

Do not use this repository as a production release until a signed release and checksum are published.

## Public project principles

- Local business files should remain on the user's device unless the user explicitly enables a documented integration.
- Local services should bind to the loopback interface by default.
- Credentials and tokens must not be committed or printed by production builds.
- Tests and documentation must use synthetic data, reserved test domains, and non-production addresses.
- Server deployment configuration, private certificates, proxy credentials, and private repository history are outside the public repository scope.

## Planned repository scope

The public repository is intended to contain:

- the Windows local Agent source code;
- local report processing and dashboard code;
- synthetic automated tests;
- Windows build and installer scripts;
- public security, privacy, installation, and release documentation.

It will not contain private fixed-egress server deployment configuration or real business data.

## Security

Please read [SECURITY.md](SECURITY.md) before reporting a vulnerability. Never place secrets or real business data in a public issue.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE) for attribution information.
