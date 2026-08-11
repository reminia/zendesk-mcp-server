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

- Transcript truncation now preserves both the initial report and newest
  customer/agent interactions.
- Tool schemas and report prompts now enforce the same digest, autocomplete,
  and report limits as runtime behavior.
- Search pagination stops at Zendesk's 1,000-result ceiling with actionable
  guidance instead of exposing invalid next pages.
- Severity discovery covers common `p1`-`p5` and `s1`-`s5` tag conventions.
- Reporting prompts explicitly treat ticket content as untrusted data.
