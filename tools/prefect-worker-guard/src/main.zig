const std = @import("std");
const builtin = @import("builtin");

const posix = std.posix;

const Config = struct {
    source_bytes: []u8,
    worker_command: []const []const u8,
    worker_name: []const u8 = "prefect-worker",
    work_pool_name: []const u8 = "default",
    check_interval_seconds: u64 = 30,
    graceful_shutdown_seconds: u64 = 60,
    memory_soft_bytes: ?u64 = null,
    memory_hard_bytes: ?u64 = null,
    tasks_soft: ?u64 = null,
    tasks_hard: ?u64 = null,
    systemd_unit: ?[]const u8 = null,
};

const Snapshot = struct {
    memory_current_bytes: ?u64 = null,
    tasks_current: ?u64 = null,
};

const RestartReason = struct {
    threshold_name: []const u8,
    threshold_value: u64,
    observed_value: u64,
};

const JournalRestart = struct {
    pattern: []const u8,
};

const WorkerState = struct {
    child: *std.process.Child,
    done: std.atomic.Value(bool) = .init(false),
    term: std.process.Child.Term = .{ .unknown = 0 },
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
    std.debug.print("  CHECK_INTERVAL_SECONDS=30\n  GRACEFUL_SHUTDOWN_SECONDS=60\n", .{});
    std.debug.print("  MEMORY_SOFT_BYTES=12884901888\n  MEMORY_HARD_BYTES=19327352832\n", .{});
    std.debug.print("  TASKS_SOFT=500\n  TASKS_HARD=800\n", .{});
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
        } else if (std.mem.eql(u8, key, "GRACEFUL_SHUTDOWN_SECONDS")) {
            config.graceful_shutdown_seconds = try parsePositiveU64(value);
        } else if (std.mem.eql(u8, key, "MEMORY_SOFT_BYTES")) {
            config.memory_soft_bytes = try parsePositiveU64(value);
        } else if (std.mem.eql(u8, key, "MEMORY_HARD_BYTES")) {
            config.memory_hard_bytes = try parsePositiveU64(value);
        } else if (std.mem.eql(u8, key, "TASKS_SOFT")) {
            config.tasks_soft = try parsePositiveU64(value);
        } else if (std.mem.eql(u8, key, "TASKS_HARD")) {
            config.tasks_hard = try parsePositiveU64(value);
        } else if (std.mem.eql(u8, key, "SYSTEMD_UNIT")) {
            config.systemd_unit = value;
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
                cleanupGroup(io, pgid);
                gpa.destroy(child);
                break;
            }

            const snapshot = collectSnapshot(gpa, io, config, pgid);
            if (journalRestartReason(gpa, io, config, started_at)) |reason| {
                logEvent("journal-restart-signal", config, pid, pgid, snapshot, null);
                logJournalRestart(config, pid, pgid, reason);
                terminateGroup(io, pgid, config.graceful_shutdown_seconds);
                while (!state.done.load(.acquire)) sleepSeconds(io, 1);
                waiter.join();
                gpa.destroy(child);
                break;
            }

            if (restartReason(config, snapshot)) |reason| {
                logEvent("resource-threshold-exceeded", config, pid, pgid, snapshot, null);
                logRestart(config, pid, pgid, reason);
                terminateGroup(io, pgid, config.graceful_shutdown_seconds);
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

fn restartReason(config: *const Config, snapshot: Snapshot) ?RestartReason {
    if (snapshot.memory_current_bytes) |observed| {
        if (config.memory_hard_bytes) |limit| {
            if (observed > limit) return .{ .threshold_name = "memory_hard_bytes", .threshold_value = limit, .observed_value = observed };
        }
        if (config.memory_soft_bytes) |limit| {
            if (observed > limit) return .{ .threshold_name = "memory_soft_bytes", .threshold_value = limit, .observed_value = observed };
        }
    }
    if (snapshot.tasks_current) |observed| {
        if (config.tasks_hard) |limit| {
            if (observed > limit) return .{ .threshold_name = "tasks_hard", .threshold_value = limit, .observed_value = observed };
        }
        if (config.tasks_soft) |limit| {
            if (observed > limit) return .{ .threshold_name = "tasks_soft", .threshold_value = limit, .observed_value = observed };
        }
    }
    return null;
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

fn terminateGroup(io: std.Io, pgid: posix.pid_t, graceful_seconds: u64) void {
    const group_pid = -pgid;
    posix.kill(group_pid, posix.SIG.TERM) catch {};

    var remaining = graceful_seconds;
    while (remaining > 0) : (remaining -= 1) {
        if (!processAlive(pgid)) return;
        sleepSeconds(io, 1);
    }

    posix.kill(group_pid, posix.SIG.KILL) catch {};
}

fn cleanupGroup(io: std.Io, pgid: posix.pid_t) void {
    const group_pid = -pgid;
    posix.kill(group_pid, posix.SIG.TERM) catch {};
    sleepSeconds(io, 1);
    posix.kill(group_pid, posix.SIG.KILL) catch {};
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

fn logRestart(config: *const Config, pid: posix.pid_t, pgid: posix.pid_t, reason: RestartReason) void {
    std.debug.print(
        "{{\"event\":\"prefect.worker.restart-requested\",\"worker_name\":\"{s}\",\"work_pool_name\":\"{s}\",\"worker_pid\":{},\"process_group_id\":{},\"threshold_name\":\"{s}\",\"threshold_value\":{},\"observed_value\":{}}}\n",
        .{ config.worker_name, config.work_pool_name, pid, pgid, reason.threshold_name, reason.threshold_value, reason.observed_value },
    );
}

fn logJournalRestart(config: *const Config, pid: posix.pid_t, pgid: posix.pid_t, reason: JournalRestart) void {
    std.debug.print(
        "{{\"event\":\"prefect.worker.restart-requested\",\"worker_name\":\"{s}\",\"work_pool_name\":\"{s}\",\"worker_pid\":{},\"process_group_id\":{},\"threshold_name\":\"worker_channel_journal_pattern\",\"pattern\":\"{s}\"}}\n",
        .{ config.worker_name, config.work_pool_name, pid, pgid, reason.pattern },
    );
}

fn sleepSeconds(io: std.Io, seconds: u64) void {
    const nanos: i96 = @intCast(seconds * std.time.ns_per_s);
    std.Io.sleep(io, .fromNanoseconds(nanos), .awake) catch {};
}
