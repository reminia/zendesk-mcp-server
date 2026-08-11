# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Zendesk Search API filtering by tags, dates, priority, status, group,
  assignee, organization, satisfaction, and text, including relative date
  ranges and count-only previews.
- Grounded filter discovery using account tags with usage counts,
  organizations, groups, and detected severity-tag conventions.
- Compact ticket digests with objective support metrics and optional cleaned,
  role-labelled transcripts.
- `zendesk://filter-vocabulary` resource for commonly used filter values.
- Prompts for clarified ticket search, sentiment analysis, priority reporting,
  and unhappy-customer support reporting.
- Optional `ZENDESK_DEFAULT_GROUP` configuration.

### Changed

- Expanded the README with filtering, digest, resource, and reporting guidance.
- Declared `requests` and `cachetools` as direct runtime dependencies.

### Removed

- None.

### Fixed

- None.
