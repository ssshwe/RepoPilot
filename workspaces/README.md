# Local test repositories

Put each repository in its own direct child directory, for example
`workspaces/demo-repository/`. The scan API accepts the child name, not a path.
It does not scan this README when you request `demo-repository`.

`demo-repository` is a small, hand-written code fixture for learning scanning,
chunking and later retrieval. Its files are real local inputs; it is not an
imported production project or a complete running application. Do not place
real credentials in test repositories.
