# public-corpus-retrieval Specification

### Requirement: Public corpus scope retrieval matches legacy untagged chunks

When retrieval is scoped to the public law library (via `folders=["__public__"]` or `corpus_scope="public"`), the system SHALL match chunks whose `metadata.corpus` equals `"public"` **OR** whose `metadata.corpus` field is absent (legacy public chunks built without a corpus tag). The system MUST NOT match user-library or case chunks under a public scope.

#### Scenario: Public library retrieval with legacy chunks

- **WHEN** a search is scoped to `folders=["__public__"]`
- **THEN** the search returns chunks from the public law corpus that lack a `corpus` field, and does not return any chunk with `corpus="user"` or `corpus="case"`

#### Scenario: Public scope via corpus_scope parameter

- **WHEN** a search is executed with `corpus_scope="public"` (knowledge-base Q&A "公共法律库" scope)
- **THEN** the search returns the same public corpus results as the `folders=["__public__"]` path

#### Scenario: Combined public and user folder scope

- **WHEN** a search is scoped to `["__public__", "<user-folder>"]`
- **THEN** the search returns legacy public chunks (missing `corpus`) together with the specified user folder's chunks, and excludes user chunks outside that folder

#### Scenario: User-library retrieval unaffected

- **WHEN** a search is scoped to a user folder only
- **THEN** the search still requires `metadata.corpus="user"` with matching `user_id` and `folder`, and does not return public chunks
