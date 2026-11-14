---
name: frameflow-engineering
description: Maintain engineering quality when developing, fixing, reviewing, or refactoring FrameFlow's PySide6 desktop UI, media processing, AI Agent tools, or tests.
---

# FrameFlow engineering

## Before editing

Read the relevant implementation, callers, and existing tests. Identify which object owns the state, which existing API can be reused, and how the change reaches the desktop entry point. Trace Qt connections, worker completion, cancellation, shutdown, and any state shared by the three workspaces. Compare a local extension with a component or service only where it would change ownership or dependencies. Keep dependencies directed toward stable contracts and domain types.

Treat size as a review signal. Review a file after about 500 lines, aim for 80–100 lines per ordinary function and 300–500 per ordinary business class, and aim for 800–1200 lines per UI coordinator. Review any class over 1500 lines explicitly. Extract a coherent responsibility with its own state and narrow inputs. Keep cohesive larger code when splitting would obscure its behavior. Avoid mixins, private attribute proxies, and new abstraction layers that only redistribute line counts.

## PySide6 and media boundaries

- Layouts and widgets own rendering and user input. Playback and preview controllers own timers and decoding lifetimes. Timeline and editing state own clips, selections or undo according to their actual coupling.
- Keep FFmpeg commands, temporary files, publishing and cancellation with the media and task layers. Keep expensive work off the GUI thread. Send results back with queued Qt signals. Handle stale results and closing objects before updating widgets. Reparenting the shared output settings must preserve the same widgets and per-file values.
- Keep media queue identity and order centralized. Use explicit methods or signals between workspaces. Agent tool dispatch must preserve the allowlist, path authorization, revalidation and user confirmation. The model cannot launch a conversion through an alternative route.
- Keep errors meaningful at the boundary that can handle them. Verify resource cleanup after cancellation and partial failure.

## Evidence and review

For substantive changes, explain the actual problem, chosen design, alternatives and tradeoffs, behavior at risk, and how the evidence verifies it. Record important decisions in an existing suitable development document. Attribute developer decisions only when explicitly made by the developer.

Select tests by risk. Use pure tests for state and error cases, integration tests for service boundaries, real FFmpeg and FFprobe or Pillow for media output, and Qt or Windows interaction for viewport geometry, DPI, drag/drop and transitions. Exercise invalid parameters, refused authorization, stale plans and cancellation when Agent code changes. Keep existing assertions and investigate failures before changing a fixture. Report skipped or unexecuted scenarios separately from passing ones.

Before handing off, inspect changed classes and functions, duplicate logic, import cycles, cross-module state access, error paths and resource ownership. Run affected tests, compare significant dependency and complexity changes, and name the remaining debt. Apply the full suite for changes that cross workspaces or editor lifecycles.
