const std = @import("std");
const builtin = @import("builtin");

const posix = std.posix;

const Config = struct {
    source_bytes: []u8,
    worker_command: []const []const u8,
    worker_name: []const u8 = "prefect-worker",
    work_pool_name: []const u8 = "default",
    check_interval_seconds: u64 = 30,
    systemd_unit: ?[]const u8 = null,
    healthcheck_url: ?[]const u8 = null,
    healthcheck_startup_grace_seconds: u64 = 120,
    healthcheck_unhealthy_retries: u64 = 3,
    terminal_descendant_check_seconds: u64 = 60,
    terminal_descendant_terminate_seconds: u64 = 10,
};

const Snapshot = struct {
    memory_current_bytes: ?u64 = null,
    tasks_current: ?u64 = null,
};

const JournalRestart = struct {
    pattern: []const u8,
};

const WorkerState = struct {
    child: *std.process.Child,
    done: std.atomic.Value(bool) = .init(false),
    term: std.process.Child.Term = .{ .unknown = 0 },
};

const FlowProcess = struct {
    pid: posix.pid_t,
    flow_run_id: []u8,
    flow_name: ?[]u8,
};

pub fn main(init: std.process.Init) !void {
    const gpa = init.gpa;
    const io = init.io;

    var it = try std.process.Args.Iterator.initAllocator(init.minimal.args, gpa);
    defer it.deinit();

    _ = it.next();
    const config_path = parseArgs(&it) catch |err| {
        try printUsage(err);
        if (err == error.HelpRequested) return;
        return err;
    };

    var config = try loadConfig(gpa, io, config_path);
    defer freeConfig(gpa, config);

    if (config.worker_command.len == 0) return error.MissingWorkerCommand;

    try runGuard(gpa, io, &config);
}

fn parseArgs(it: *std.process.Args.Iterator) ![]const u8 {
    var config_path: ?[]const u8 = null;
    while (it.next()) |arg_z| {
        const arg = arg_z[0..arg_z.len];
        if (std.mem.eql(u8, arg, "--config")) {
            config_path = it.next() orelse return error.MissingConfigValue;
        } else if (std.mem.startsWith(u8, arg, "--config=")) {
            config_path = arg["--config=".len..];
        } else if (std.mem.eql(u8, arg, "--help") or std.mem.eql(u8, arg, "-h")) {
            return error.HelpRequested;
        } else {
            return error.UnknownArgument;
        }
    }
    return config_path orelse error.MissingConfig;
}

fn printUsage(err: anyerror) !void {
    std.debug.print("prefect-worker-guard: {s}\n", .{@errorName(err)});
    std.debug.print("usage: prefect-worker-guard --config /path/to/prefect-worker-guard.env\n", .{});
    std.debug.print("\nconfig keys:\n", .{});
    std.debug.print("  WORKER_COMMAND=/path/to/uv\\0run\\0--with\\0prefect==3.7.2\\0prefect\\0worker\\0start\\0...\n", .{});
    std.debug.print("  WORKER_NAME=heavypad\n  WORK_POOL_NAME=home-pool\n  SYSTEMD_UNIT=prefect-home-worker.service\n", .{});
    std.debug.print("  CHECK_INTERVAL_SECONDS=30\n", .{});
    std.debug.print("  HEALTHCHECK_URL=http://127.0.0.1:8080/health\n", .{});
    std.debug.print("  HEALTHCHECK_STARTUP_GRACE_SECONDS=120\n  HEALTHCHECK_UNHEALTHY_RETRIES=3\n", .{});
    std.debug.print("  TERMINAL_DESCENDANT_CHECK_SECONDS=60\n  TERMINAL_DESCENDANT_TERMINATE_SECONDS=10\n", .{});
}

fn loadConfig(gpa: std.mem.Allocator, io: std.Io, path: []const u8) !Config {
    const bytes = try std.Io.Dir.cwd().readFileAlloc(io, path, gpa, .limited(128 * 1024));
    errdefer gpa.free(bytes);

    var config = Config{ .source_bytes = bytes, .worker_command = &.{} };
    var command_set = false;

    var lines = std.mem.splitScalar(u8, bytes, '\n');
    while (lines.next()) |raw_line| {
        const line = std.mem.trim(u8, raw_line, " \t\r");
        if (line.len == 0 or line[0] == '#') continue;

        const eq = std.mem.indexOfScalar(u8, line, '=') orelse return error.InvalidConfigLine;
        const key = std.mem.trim(u8, line[0..eq], " \t");
        const value = std.mem.trim(u8, line[eq + 1 ..], " \t");

        if (std.mem.eql(u8, key, "WORKER_COMMAND")) {
            config.worker_command = try parseCommand(gpa, value);
            command_set = true;
        } else if (std.mem.eql(u8, key, "WORKER_NAME")) {
            config.worker_name = value;
        } else if (std.mem.eql(u8, key, "WORK_POOL_NAME")) {
            config.work_pool_name = value;
        } else if (std.mem.eql(u8, key, "CHECK_INTERVAL_SECONDS")) {
            config.check_interval_seconds = try parsePositiveU64(value);
        } else if (std.mem.eql(u8, key, "SYSTEMD_UNIT")) {
            config.systemd_unit = value;
        } else if (std.mem.eql(u8, key, "HEALTHCHECK_URL")) {
            config.healthcheck_url = value;
        } else if (std.mem.eql(u8, key, "HEALTHCHECK_STARTUP_GRACE_SECONDS")) {
            config.healthcheck_startup_grace_seconds = try parsePositiveU64(value);
        } else if (std.mem.eql(u8, key, "HEALTHCHECK_UNHEALTHY_RETRIES")) {
            config.healthcheck_unhealthy_retries = try parsePositiveU64(value);
        } else if (std.mem.eql(u8, key, "TERMINAL_DESCENDANT_CHECK_SECONDS")) {
            config.terminal_descendant_check_seconds = try parsePositiveU64(value);
        } else if (std.mem.eql(u8, key, "TERMINAL_DESCENDANT_TERMINATE_SECONDS")) {
            config.terminal_descendant_terminate_seconds = try parsePositiveU64(value);
        } else {
            return error.UnknownConfigKey;
        }
    }

    if (!command_set) return error.MissingWorkerCommand;
    return config;
}

fn freeConfig(gpa: std.mem.Allocator, config: Config) void {
    for (config.worker_command) |part| gpa.free(part);
    gpa.free(config.worker_command);
    gpa.free(config.source_bytes);
}

fn parseCommand(gpa: std.mem.Allocator, value: []const u8) ![]const []const u8 {
    var parts: std.ArrayList([]const u8) = .empty;
    errdefer {
        for (parts.items) |part| gpa.free(part);
        parts.deinit(gpa);
    }

    if (std.mem.indexOfScalar(u8, value, 0) != null) {
        var split = std.mem.splitScalar(u8, value, 0);
        while (split.next()) |part| {
            if (part.len == 0) continue;
            try parts.append(gpa, try gpa.dupe(u8, part));
        }
    } else {
        var split = std.mem.tokenizeAny(u8, value, " \t");
        while (split.next()) |part| {
            try parts.append(gpa, try gpa.dupe(u8, part));
        }
    }

    return try parts.toOwnedSlice(gpa);
}

fn parsePositiveU64(value: []const u8) !u64 {
    const parsed = try std.fmt.parseInt(u64, value, 10);
    if (parsed == 0) return error.InvalidZeroValue;
    return parsed;
}

fn runGuard(gpa: std.mem.Allocator, io: std.Io, config: *const Config) !void {
    while (true) {
        const child = try gpa.create(std.process.Child);
        errdefer gpa.destroy(child);
        child.* = try spawnWorker(io, config);
        const pid = child.id.?;
        const pgid = pid;
        const started_at = unixSeconds();
        var last_terminal_descendant_check: i64 = 0;
        var unhealthy_checks: u64 = 0;
        var state = WorkerState{ .child = child };
        var waiter = std.Thread.spawn(.{}, waitForWorker, .{ io, &state }) catch |err| {
            child.kill(io);
            gpa.destroy(child);
            return err;
        };
        logEvent("worker-started", config, pid, pgid, null, null);

        while (true) {
            sleepSeconds(io, config.check_interval_seconds);

            if (state.done.load(.acquire)) {
                waiter.join();
                logEvent("worker-exited", config, pid, pgid, null, state.term);
                gpa.destroy(child);
                break;
            }

            const snapshot = collectSnapshot(gpa, io, config, pgid);
            const now = unixSeconds();
            if (config.terminal_descendant_check_seconds > 0 and now > 0 and (last_terminal_descendant_check == 0 or now - last_terminal_descendant_check >= config.terminal_descendant_check_seconds)) {
                last_terminal_descendant_check = now;
                cleanupTerminalDescendants(gpa, io, config, pid, pgid);
            }

            if (healthcheckUnhealthy(gpa, io, config, started_at)) {
                unhealthy_checks += 1;
                logHealthcheckFailure(config, pid, pgid, unhealthy_checks);
                if (unhealthy_checks >= config.healthcheck_unhealthy_retries) {
                    logEvent("healthcheck-failed", config, pid, pgid, snapshot, null);
                    logHealthcheckRestart(config, pid, pgid, unhealthy_checks);
                    terminateWorkerOnly(pid);
                    while (!state.done.load(.acquire)) sleepSeconds(io, 1);
                    waiter.join();
                    gpa.destroy(child);
                    break;
                }
            } else {
                unhealthy_checks = 0;
            }

            if (journalRestartReason(gpa, io, config, started_at)) |reason| {
                logEvent("journal-restart-signal", config, pid, pgid, snapshot, null);
                logJournalRestart(config, pid, pgid, reason);
                terminateWorkerOnly(pid);
                while (!state.done.load(.acquire)) sleepSeconds(io, 1);
                waiter.join();
                gpa.destroy(child);
                break;
            }

            logEvent("resource-observed", config, pid, pgid, snapshot, null);
        }
    }
}

fn waitForWorker(io: std.Io, state: *WorkerState) void {
    state.term = state.child.wait(io) catch .{ .unknown = 1 };
    state.done.store(true, .release);
}

fn spawnWorker(io: std.Io, config: *const Config) !std.process.Child {
    return try std.process.spawn(io, .{
        .argv = config.worker_command,
        .stdin = .inherit,
        .stdout = .inherit,
        .stderr = .inherit,
        .pgid = 0,
    });
}

fn processAlive(pid: posix.pid_t) bool {
    const rc = std.c.kill(pid, @enumFromInt(0));
    if (rc == 0) return true;
    return switch (posix.errno(rc)) {
        .SRCH => false,
        .PERM => true,
        else => true,
    };
}

fn collectSnapshot(gpa: std.mem.Allocator, io: std.Io, config: *const Config, pgid: posix.pid_t) Snapshot {
    var snapshot = Snapshot{};

    if (builtin.os.tag == .linux) {
        if (config.systemd_unit) |unit| {
            snapshot.memory_current_bytes = readSystemdProperty(gpa, io, unit, "MemoryCurrent") catch null;
            snapshot.tasks_current = readSystemdProperty(gpa, io, unit, "TasksCurrent") catch null;
        }
    }

    _ = pgid;
    return snapshot;
}

fn cleanupTerminalDescendants(gpa: std.mem.Allocator, io: std.Io, config: *const Config, worker_pid: posix.pid_t, pgid: posix.pid_t) void {
    if (builtin.os.tag != .linux) return;

    const flow_processes = collectFlowProcesses(gpa, io, config, pgid) catch |err| {
        logTerminalDescendantScanError(config, worker_pid, pgid, @errorName(err));
        return;
    };
    defer freeFlowProcesses(gpa, flow_processes);
    if (flow_processes.len > 0) logTerminalDescendantScan(config, worker_pid, pgid, flow_processes.len);

    for (flow_processes, 0..) |flow_process, index| {
        if (seenFlowRun(flow_processes[0..index], flow_process.flow_run_id)) continue;

        const state_type = flowRunStateType(gpa, io, flow_process.flow_run_id) catch null;
        if (state_type == null) continue;
        defer gpa.free(state_type.?);

        if (!isTerminalState(state_type.?)) continue;

        const affected = countFlowRunProcesses(flow_processes, flow_process.flow_run_id);
        logTerminalDescendantObserved(config, worker_pid, pgid, flow_process.flow_run_id, flow_process.flow_name, state_type.?, affected);
        terminateFlowRunProcesses(io, flow_processes, flow_process.flow_run_id, config.terminal_descendant_terminate_seconds);
        logTerminalDescendantTerminated(config, worker_pid, pgid, flow_process.flow_run_id, state_type.?, affected);
    }
}

fn collectFlowProcesses(gpa: std.mem.Allocator, io: std.Io, config: *const Config, pgid: posix.pid_t) ![]FlowProcess {
    var proc_dir = try std.Io.Dir.openDirAbsolute(io, "/proc", .{ .iterate = true });
    defer proc_dir.close(io);

    var processes: std.ArrayList(FlowProcess) = .empty;
    errdefer {
        for (processes.items) |process| {
            gpa.free(process.flow_run_id);
            if (process.flow_name) |flow_name| gpa.free(flow_name);
        }
        processes.deinit(gpa);
    }

    var iterator = proc_dir.iterate();
    while (try iterator.next(io)) |entry| {
        const pid_int = std.fmt.parseInt(i32, entry.name, 10) catch continue;
        const pid: posix.pid_t = @intCast(pid_int);
        if (config.systemd_unit) |unit| {
            if (!processBelongsToSystemdUnit(gpa, io, pid, unit)) continue;
        } else {
            const process_pgid = readProcessGroupId(gpa, io, pid) catch continue;
            if (process_pgid != pgid) continue;
        }

        const process = readFlowProcess(gpa, io, pid) catch continue;
        if (process) |flow_process| try processes.append(gpa, flow_process);
    }

    return try processes.toOwnedSlice(gpa);
}

fn processBelongsToSystemdUnit(gpa: std.mem.Allocator, io: std.Io, pid: posix.pid_t, unit: []const u8) bool {
    var path_buf: [64]u8 = undefined;
    const path = std.fmt.bufPrint(&path_buf, "/proc/{}/cgroup", .{pid}) catch return false;
    const cgroup = readFileStreamingAlloc(gpa, io, path, 64 * 1024) catch return false;
    defer gpa.free(cgroup);
    return cgroupContainsUnit(cgroup, unit);
}

fn cgroupContainsUnit(cgroup: []const u8, unit: []const u8) bool {
    return std.mem.indexOf(u8, cgroup, unit) != null;
}

fn readProcessGroupId(gpa: std.mem.Allocator, io: std.Io, pid: posix.pid_t) !posix.pid_t {
    var path_buf: [64]u8 = undefined;
    const path = try std.fmt.bufPrint(&path_buf, "/proc/{}/stat", .{pid});
    const stat = try readFileStreamingAlloc(gpa, io, path, 4096);
    defer gpa.free(stat);

    const comm_end = std.mem.lastIndexOf(u8, stat, ") ") orelse return error.InvalidProcStat;
    var fields = std.mem.tokenizeScalar(u8, stat[comm_end + 2 ..], ' ');
    _ = fields.next() orelse return error.InvalidProcStat; // state
    _ = fields.next() orelse return error.InvalidProcStat; // parent pid
    const pgrp = fields.next() orelse return error.InvalidProcStat;
    return @intCast(try std.fmt.parseInt(i32, pgrp, 10));
}

fn readFlowProcess(gpa: std.mem.Allocator, io: std.Io, pid: posix.pid_t) !?FlowProcess {
    var path_buf: [64]u8 = undefined;
    const path = try std.fmt.bufPrint(&path_buf, "/proc/{}/environ", .{pid});
    const environ = readFileStreamingAlloc(gpa, io, path, 256 * 1024) catch return null;
    defer gpa.free(environ);

    const flow_run_id = envValue(environ, "PREFECT__FLOW_RUN_ID") orelse return null;
    const flow_name = envValue(environ, "PREFECT__FLOW_NAME");

    return .{
        .pid = pid,
        .flow_run_id = try gpa.dupe(u8, flow_run_id),
        .flow_name = if (flow_name) |name| try gpa.dupe(u8, name) else null,
    };
}

fn readFileStreamingAlloc(gpa: std.mem.Allocator, io: std.Io, path: []const u8, limit: usize) ![]u8 {
    var file = try std.Io.Dir.openFileAbsolute(io, path, .{});
    defer file.close(io);

    var contents: std.ArrayList(u8) = .empty;
    errdefer contents.deinit(gpa);

    while (contents.items.len < limit) {
        var chunk_buf: [4096]u8 = undefined;
        const remaining = limit - contents.items.len;
        const chunk = chunk_buf[0..@min(chunk_buf.len, remaining)];
        const read_len = file.readStreaming(io, &.{chunk}) catch |err| switch (err) {
            error.EndOfStream => break,
            else => return err,
        };
        if (read_len == 0) break;
        try contents.appendSlice(gpa, chunk[0..read_len]);
    }

    return try contents.toOwnedSlice(gpa);
}

fn envValue(environ: []const u8, key: []const u8) ?[]const u8 {
    var entries = std.mem.splitScalar(u8, environ, 0);
    while (entries.next()) |entry| {
        if (entry.len <= key.len or entry[key.len] != '=') continue;
        if (std.mem.eql(u8, entry[0..key.len], key)) return entry[key.len + 1 ..];
    }
    return null;
}

fn flowRunStateType(gpa: std.mem.Allocator, io: std.Io, flow_run_id: []const u8) !?[]u8 {
    const api_url = envVarOwned(gpa, "PREFECT_API_URL") catch return null;
    defer gpa.free(api_url);
    const auth = envVarOwned(gpa, "PREFECT_API_AUTH_STRING") catch return null;
    defer gpa.free(auth);

    const separator: []const u8 = if (std.mem.endsWith(u8, api_url, "/")) "" else "/";
    const url = try std.fmt.allocPrint(gpa, "{s}{s}flow_runs/{s}", .{ api_url, separator, flow_run_id });
    defer gpa.free(url);

    const argv = [_][]const u8{
        "curl",
        "-fsS",
        "--max-time",
        "10",
        "-u",
        auth,
        url,
    };
    const result = std.process.run(gpa, io, .{
        .argv = &argv,
        .stdout_limit = .limited(256 * 1024),
        .stderr_limit = .limited(16 * 1024),
    }) catch return null;
    defer gpa.free(result.stdout);
    defer gpa.free(result.stderr);

    switch (result.term) {
        .exited => |code| if (code != 0) return null,
        else => return null,
    }

    const state_type = jsonStringField(result.stdout, "state_type") orelse return null;
    return try gpa.dupe(u8, state_type);
}

fn envVarOwned(gpa: std.mem.Allocator, comptime name: [:0]const u8) ![]u8 {
    const value = std.c.getenv(name.ptr) orelse return error.MissingEnvironmentVariable;
    return try gpa.dupe(u8, std.mem.span(value));
}

fn jsonStringField(json: []const u8, field_name: []const u8) ?[]const u8 {
    var name_buf: [128]u8 = undefined;
    if (field_name.len + 2 > name_buf.len) return null;
    name_buf[0] = '"';
    @memcpy(name_buf[1 .. 1 + field_name.len], field_name);
    name_buf[1 + field_name.len] = '"';
    const needle = name_buf[0 .. field_name.len + 2];

    const field_start = std.mem.indexOf(u8, json, needle) orelse return null;
    const colon_rel = std.mem.indexOfScalar(u8, json[field_start + needle.len ..], ':') orelse return null;
    var index = field_start + needle.len + colon_rel + 1;
    while (index < json.len and std.ascii.isWhitespace(json[index])) : (index += 1) {}
    if (index >= json.len or json[index] != '"') return null;
    index += 1;
    const value_start = index;
    while (index < json.len and json[index] != '"') : (index += 1) {}
    if (index >= json.len) return null;
    return json[value_start..index];
}

fn isTerminalState(state_type: []const u8) bool {
    return std.mem.eql(u8, state_type, "COMPLETED") or
        std.mem.eql(u8, state_type, "FAILED") or
        std.mem.eql(u8, state_type, "CRASHED") or
        std.mem.eql(u8, state_type, "CANCELLED");
}

fn seenFlowRun(processes: []const FlowProcess, flow_run_id: []const u8) bool {
    for (processes) |process| {
        if (std.mem.eql(u8, process.flow_run_id, flow_run_id)) return true;
    }
    return false;
}

fn countFlowRunProcesses(processes: []const FlowProcess, flow_run_id: []const u8) u64 {
    var count: u64 = 0;
    for (processes) |process| {
        if (std.mem.eql(u8, process.flow_run_id, flow_run_id)) count += 1;
    }
    return count;
}

fn terminateFlowRunProcesses(io: std.Io, processes: []const FlowProcess, flow_run_id: []const u8, graceful_seconds: u64) void {
    for (processes) |process| {
        if (!std.mem.eql(u8, process.flow_run_id, flow_run_id)) continue;
        posix.kill(process.pid, posix.SIG.TERM) catch {};
    }

    var remaining = graceful_seconds;
    while (remaining > 0) : (remaining -= 1) {
        var any_alive = false;
        for (processes) |process| {
            if (!std.mem.eql(u8, process.flow_run_id, flow_run_id)) continue;
            if (processAlive(process.pid)) any_alive = true;
        }
        if (!any_alive) return;
        sleepSeconds(io, 1);
    }

    for (processes) |process| {
        if (!std.mem.eql(u8, process.flow_run_id, flow_run_id)) continue;
        if (processAlive(process.pid)) posix.kill(process.pid, posix.SIG.KILL) catch {};
    }
}

fn freeFlowProcesses(gpa: std.mem.Allocator, processes: []const FlowProcess) void {
    for (processes) |process| {
        gpa.free(process.flow_run_id);
        if (process.flow_name) |flow_name| gpa.free(flow_name);
    }
    gpa.free(processes);
}

fn readSystemdProperty(gpa: std.mem.Allocator, io: std.Io, unit: []const u8, property: []const u8) !u64 {
    const argv = [_][]const u8{ "systemctl", "show", unit, "-P", property };
    const result = std.process.run(gpa, io, .{
        .argv = &argv,
        .stdout_limit = .limited(4096),
        .stderr_limit = .limited(4096),
    }) catch return error.SystemctlFailed;
    defer gpa.free(result.stdout);
    defer gpa.free(result.stderr);

    switch (result.term) {
        .exited => |code| if (code != 0) return error.SystemctlFailed,
        else => return error.SystemctlFailed,
    }
    const value = std.mem.trim(u8, result.stdout, " \t\r\n");
    if (value.len == 0 or std.mem.eql(u8, value, "[not set]")) return error.NotSet;
    return try std.fmt.parseInt(u64, value, 10);
}

fn healthcheckUnhealthy(gpa: std.mem.Allocator, io: std.Io, config: *const Config, started_at: i64) bool {
    const url = config.healthcheck_url orelse return false;

    const now = unixSeconds();
    if (now > started_at and @as(u64, @intCast(now - started_at)) < config.healthcheck_startup_grace_seconds) {
        return false;
    }

    const argv = [_][]const u8{
        "curl",
        "-fsS",
        "--max-time",
        "5",
        url,
    };
    const result = std.process.run(gpa, io, .{
        .argv = &argv,
        .stdout_limit = .limited(4096),
        .stderr_limit = .limited(4096),
    }) catch return true;
    defer gpa.free(result.stdout);
    defer gpa.free(result.stderr);

    return switch (result.term) {
        .exited => |code| code != 0,
        else => true,
    };
}

fn journalRestartReason(gpa: std.mem.Allocator, io: std.Io, config: *const Config, started_at: i64) ?JournalRestart {
    const unit = config.systemd_unit orelse return null;
    if (builtin.os.tag != .linux) return null;

    var since_buf: [64]u8 = undefined;
    const since = std.fmt.bufPrint(&since_buf, "@{}", .{started_at}) catch return null;
    const argv = [_][]const u8{
        "journalctl",
        "-u",
        unit,
        "--since",
        since,
        "-n",
        "300",
        "--no-pager",
        "-o",
        "cat",
    };
    const result = std.process.run(gpa, io, .{
        .argv = &argv,
        .stdout_limit = .limited(256 * 1024),
        .stderr_limit = .limited(16 * 1024),
    }) catch return null;
    defer gpa.free(result.stdout);
    defer gpa.free(result.stderr);

    switch (result.term) {
        .exited => |code| if (code != 0) return null,
        else => return null,
    }

    const fatal_patterns = [_][]const u8{
        "WorkerChannelProtocolHandler._heartbeat_loop",
        "keepalive ping timeout",
    };
    for (fatal_patterns) |pattern| {
        if (std.mem.indexOf(u8, result.stdout, pattern) != null) {
            return .{ .pattern = pattern };
        }
    }
    return null;
}

fn unixSeconds() i64 {
    var ts: posix.timespec = undefined;
    switch (posix.errno(posix.system.clock_gettime(.REALTIME, &ts))) {
        .SUCCESS => return @intCast(ts.sec),
        else => return 0,
    }
}

fn terminateWorkerOnly(pid: posix.pid_t) void {
    // Flow-run processes share the worker's process group. Killing the group
    // turns a scheduler/control-plane failure into collateral flow failures.
    // SIGKILL only the unhealthy worker so its independently-running flow
    // children can finish and persist their own terminal states.
    posix.kill(pid, posix.SIG.KILL) catch {};
}

fn logEvent(
    event: []const u8,
    config: *const Config,
    pid: posix.pid_t,
    pgid: posix.pid_t,
    snapshot: ?Snapshot,
    term: ?std.process.Child.Term,
) void {
    const snap = snapshot orelse Snapshot{};

    std.debug.print(
        "{{\"event\":\"prefect.worker.{s}\",\"worker_name\":\"{s}\",\"work_pool_name\":\"{s}\",\"worker_pid\":{},\"process_group_id\":{}",
        .{ event, config.worker_name, config.work_pool_name, pid, pgid },
    );

    if (snap.memory_current_bytes) |memory| std.debug.print(",\"memory_current_bytes\":{}", .{memory});
    if (snap.tasks_current) |tasks| std.debug.print(",\"tasks_current\":{}", .{tasks});
    if (term) |t| switch (t) {
        .exited => |code| std.debug.print(",\"exit_code\":{}", .{code}),
        .signal => |sig| std.debug.print(",\"signal\":{}", .{@intFromEnum(sig)}),
        .stopped => |sig| std.debug.print(",\"stopped_signal\":{}", .{@intFromEnum(sig)}),
        .unknown => |code| std.debug.print(",\"unknown_term\":{}", .{code}),
    };

    std.debug.print("}}\n", .{});
}

fn logJournalRestart(config: *const Config, pid: posix.pid_t, pgid: posix.pid_t, reason: JournalRestart) void {
    std.debug.print(
        "{{\"event\":\"prefect.worker.restart-requested\",\"worker_name\":\"{s}\",\"work_pool_name\":\"{s}\",\"worker_pid\":{},\"process_group_id\":{},\"threshold_name\":\"worker_channel_journal_pattern\",\"pattern\":\"{s}\"}}\n",
        .{ config.worker_name, config.work_pool_name, pid, pgid, reason.pattern },
    );
}

fn logHealthcheckFailure(config: *const Config, pid: posix.pid_t, pgid: posix.pid_t, failure_count: u64) void {
    std.debug.print(
        "{{\"event\":\"prefect.worker.healthcheck-failure-observed\",\"worker_name\":\"{s}\",\"work_pool_name\":\"{s}\",\"worker_pid\":{},\"process_group_id\":{},\"failure_count\":{}}}\n",
        .{ config.worker_name, config.work_pool_name, pid, pgid, failure_count },
    );
}

fn logHealthcheckRestart(config: *const Config, pid: posix.pid_t, pgid: posix.pid_t, failure_count: u64) void {
    std.debug.print(
        "{{\"event\":\"prefect.worker.restart-requested\",\"worker_name\":\"{s}\",\"work_pool_name\":\"{s}\",\"worker_pid\":{},\"process_group_id\":{},\"threshold_name\":\"local_worker_healthcheck\",\"observed_value\":{}}}\n",
        .{ config.worker_name, config.work_pool_name, pid, pgid, failure_count },
    );
}

fn logTerminalDescendantScan(config: *const Config, pid: posix.pid_t, pgid: posix.pid_t, process_count: usize) void {
    std.debug.print(
        "{{\"event\":\"prefect.worker.terminal-descendant-scan\",\"worker_name\":\"{s}\",\"work_pool_name\":\"{s}\",\"worker_pid\":{},\"process_group_id\":{},\"process_count\":{}}}\n",
        .{ config.worker_name, config.work_pool_name, pid, pgid, process_count },
    );
}

fn logTerminalDescendantScanError(config: *const Config, pid: posix.pid_t, pgid: posix.pid_t, err: []const u8) void {
    std.debug.print(
        "{{\"event\":\"prefect.worker.terminal-descendant-scan-error\",\"worker_name\":\"{s}\",\"work_pool_name\":\"{s}\",\"worker_pid\":{},\"process_group_id\":{},\"error\":\"{s}\"}}\n",
        .{ config.worker_name, config.work_pool_name, pid, pgid, err },
    );
}

fn logTerminalDescendantObserved(
    config: *const Config,
    pid: posix.pid_t,
    pgid: posix.pid_t,
    flow_run_id: []const u8,
    flow_name: ?[]const u8,
    state_type: []const u8,
    process_count: u64,
) void {
    std.debug.print(
        "{{\"event\":\"prefect.worker.terminal-descendant-observed\",\"worker_name\":\"{s}\",\"work_pool_name\":\"{s}\",\"worker_pid\":{},\"process_group_id\":{},\"flow_run_id\":\"{s}\",\"state_type\":\"{s}\",\"process_count\":{}",
        .{ config.worker_name, config.work_pool_name, pid, pgid, flow_run_id, state_type, process_count },
    );
    if (flow_name) |name| std.debug.print(",\"flow_name\":\"{s}\"", .{name});
    std.debug.print("}}\n", .{});
}

fn logTerminalDescendantTerminated(
    config: *const Config,
    pid: posix.pid_t,
    pgid: posix.pid_t,
    flow_run_id: []const u8,
    state_type: []const u8,
    process_count: u64,
) void {
    std.debug.print(
        "{{\"event\":\"prefect.worker.terminal-descendant-terminated\",\"worker_name\":\"{s}\",\"work_pool_name\":\"{s}\",\"worker_pid\":{},\"process_group_id\":{},\"flow_run_id\":\"{s}\",\"state_type\":\"{s}\",\"process_count\":{}}}\n",
        .{ config.worker_name, config.work_pool_name, pid, pgid, flow_run_id, state_type, process_count },
    );
}

test "systemd cgroup matching includes prior worker generations in the unit" {
    const cgroup = "0::/system.slice/prefect-home-worker.service\n";
    try std.testing.expect(cgroupContainsUnit(cgroup, "prefect-home-worker.service"));
    try std.testing.expect(!cgroupContainsUnit(cgroup, "other-worker.service"));
}

fn sleepSeconds(io: std.Io, seconds: u64) void {
    const nanos: i96 = @intCast(seconds * std.time.ns_per_s);
    std.Io.sleep(io, .fromNanoseconds(nanos), .awake) catch {};
}
