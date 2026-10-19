# Security policy

Report suspected vulnerabilities privately to the repository maintainers before public disclosure. Include the affected version and a small reproduction that contains no credentials or customer data.

The collector makes bounded HTTPS `GET` requests to explicitly allowlisted official hosts. It does not call inference endpoints, use API keys, authenticate, send external messages, or submit payloads. Source URLs with user info, query strings, or fragments are rejected. The JSON state stores public evidence excerpts, source URLs, hashes, health, and event history. Watchlists may contain internal owner and service names; protect state and reports accordingly and avoid publishing them as public artifacts.

The scheduled workflow requests only `contents: read`, uses no provider secrets, has a concurrency guard and a 20-minute job timeout, and uploads reports even when collection returns an error. Review third-party GitHub Actions versions under your organization's supply-chain policy.
