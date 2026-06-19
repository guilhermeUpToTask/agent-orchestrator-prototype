// src/types/forge.ts
// PR-window DTO shapes, mirroring src/api/schemas/forge.py.
// Hand-written pending openapi-ts coverage of the control-plane routes.

export interface Person {
  name: string;
  email: string | null;
  login: string | null;
  avatar_url: string | null;
}

export interface CommitNode {
  sha: string;
  parents: string[];
  summary: string;
  author: Person;
  committer: Person;
  authored_at: string;
  committed_at: string;
  refs: string[];
  pr_number: number | null;
}

export interface CommitGraph {
  nodes: CommitNode[];
  source: string;
  truncated: boolean;
  head_sha: string | null;
  dangling_parents: string[];
}

export type PrStateValue = 'open' | 'draft' | 'merged' | 'closed';

export interface PullRequest {
  number: number;
  title: string;
  state: PrStateValue;
  head_ref: string;
  base_ref: string;
  head_sha: string;
  author: Person;
  review_state: string;
  checks: string;
  requested_reviewers: string[];
  is_mergeable: boolean | null;
  created_at: string;
  updated_at: string;
  source: string;
}

export interface ForgeCapabilities {
  source: string;
  supports_prs: boolean;
  supports_reviews: boolean;
  supports_checks: boolean;
}
