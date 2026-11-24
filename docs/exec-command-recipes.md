# Exec Command Recipes

These recipes intentionally use explicit `exec_command` commands. The MCP server does not infer project type, install dependencies automatically, or choose package-manager cache policy.

## Foreground and background results

The server always exposes the same four process tools: `exec_command`,
`write_stdin`, `read_output`, and `kill_command`. It does not dynamically add a
tool after a command starts.

`exec_command` waits up to 10 seconds by default. If the command exits in that
window, the result is complete and no polling call is needed. If it is still
running, the result contains a `command_id` and an exact `next_action`, for
example:

```json
{
  "status": "running",
  "command_id": "cmd_123",
  "next_action": {
    "tool": "write_stdin",
    "arguments": {
      "command_id": "cmd_123",
      "chars": "",
      "yield_time_ms": 10000
    }
  }
}
```

Calling `write_stdin` with empty `chars` means “wait/poll”; non-empty `chars`
interacts with the process, which requires starting it with
`keep_stdin_open: true` (or `tty: true` on POSIX). `read_output` is for paging retained stdout/stderr
when a result explicitly says output was truncated (or when compact verbosity
was requested). It is not an extra step for every command.

Relative paths always resolve against the workspace root, so pass `workdir`
explicitly whenever a command must run somewhere else.

Use the external runtime `HOME`, `TMPDIR`, or `cache_dir` reported by `server_info` when you want dependency caches without adding files to the Git worktree. These shell examples assume trusted mode because they use environment expansion:

```bash
MAVEN_USER_HOME="$HOME/.cache/m2" mvn test
GRADLE_USER_HOME="$HOME/.cache/gradle" ./gradlew test
npm_config_cache="$HOME/.cache/npm" npm ci && npm test
PIP_CACHE_DIR="$HOME/.cache/pip" python -m pip install -r requirements.txt && python -m pytest
GOCACHE="$HOME/.cache/go-build" GOMODCACHE="$HOME/.cache/go-mod" go test ./...
CARGO_HOME="$HOME/.cache/cargo" cargo test
cmake -S . -B "$TMPDIR/cmake-build" && cmake --build "$TMPDIR/cmake-build" && ctest --test-dir "$TMPDIR/cmake-build"
```

Primitive toolchain checks:

```bash
java -version
javac -version
mvn -version
gcc --version
g++ --version
make --version
cmake --version
node --version
npm --version
python --version
pip --version
go version
cargo --version
rustc --version
```

If dependencies need network access, start with:

```bash
coding-tools-mcp --permission-mode trusted --workspace /path/to/repo
```


## Shared Cargo cache on Linux

To reuse an existing manual Cargo build, configure the server process
(not the command's environment argument). Example for an existing Rustup
installation and workspace `/quick/outsidesol`; replace the account path
and retain any existing read-only roots and PATH entries required by your
installation:

```bash
export CODING_TOOLS_MCP_EXEC_WRITE_ROOTS=/home/dev/.cargo
export CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS=/home/dev/.rustup
export CODING_TOOLS_MCP_SHELL_ENV_SET='{"CARGO_HOME":"/home/dev/.cargo","RUSTUP_HOME":"/home/dev/.rustup","CARGO_TARGET_DIR":"/quick/outsidesol/target","PATH":"/home/dev/.cargo/bin:/usr/local/bin:/usr/bin:/bin"}'
coding-tools-mcp --permission-mode safe --workspace /quick/outsidesol
```

The directories must already exist. Keep the manual build on the same
Cargo home, Rust toolchain, target directory, profile, features, and
compiler flags. Do not run `cargo clean` on a shared target. Run
`cargo check --locked --offline` or `cargo build --locked --offline` from
the workspace; Cargo may still build different artifacts for different
commands or profiles. Rustup must already have the required toolchain
installed; if the proxy tries to update Rustup state, use the installed
toolchain's bin directory directly and match the manual toolchain.

This setup grants access to the entire Cargo home, including executable
shims, config and any stored credentials. Only use it with trusted
workspaces/clients and a cache without secrets. Read the
[external writable roots security notes](permission-modes.md#external-writable-roots-linux)
before enabling it. Use an actual network sandbox if network isolation is
required; safe mode's network command checks are not a firewall.
