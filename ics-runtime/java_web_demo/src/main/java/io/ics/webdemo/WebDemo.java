package io.ics.webdemo;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.ics.runtime.RunResult;
import io.ics.runtime.Session;
import io.ics.runtime.ToolCallRecord;
import io.ics.runtime.Violation;
import io.javalin.Javalin;
import io.javalin.http.Context;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * ICS Runtime Java Web Demo — Javalin HTTP server.
 *
 * <p>Exposes the same REST/SSE API as the Python {@code web_demo/app.py}:
 * <pre>
 *   GET  /                      → serves index.html
 *   GET  /api/status            → API key presence + SDK versions
 *   GET  /api/leads             → 3 mock BFSI leads
 *   POST /api/qualify           → synchronous qualification (JSON)
 *   GET  /api/qualify/stream    → SSE: log events + final result
 *   GET  /api/metrics           → cumulative across all runs
 *   GET  /api/logs              → persistent SSE log stream with history replay
 * </pre>
 *
 * <p>Run:
 * <pre>
 *   cd ics-runtime/java   &amp;&amp;  mvn install -q
 *   cd ../java_web_demo   &amp;&amp;  mvn package -q
 *   ANTHROPIC_API_KEY=sk-ant-...  java -jar target/ics-runtime-web-demo.jar
 *   # → http://localhost:7862
 * </pre>
 */
public class WebDemo {

    static final int PORT = 7862;
    static final ObjectMapper JSON = new ObjectMapper();

    // Cumulative result store (thread-safe)
    static final List<Map<String, Object>> ALL_RESULTS = new CopyOnWriteArrayList<>();

    // ── Lead catalogue ────────────────────────────────────────────────────────

    static final Map<String, Map<String, Object>> LEADS;
    static {
        Map<String, Map<String, Object>> m = new LinkedHashMap<>();
        m.put("L-001", Map.of("id","L-001","company","Nexus Logistics Ltd",
                "industry","Logistics","revenue_usd",1_500_000,"dscr",1.42,
                "age_months",84,"liens_usd",0,"tier","prime"));
        m.put("L-002", Map.of("id","L-002","company","Beta Foods Inc",
                "industry","Food Service","revenue_usd",890_000,"dscr",1.18,
                "age_months",36,"liens_usd",55_000,"tier","subprime"));
        m.put("L-003", Map.of("id","L-003","company","Apex Consulting",
                "industry","Consulting","revenue_usd",250_000,"dscr",0.95,
                "age_months",18,"liens_usd",0,"tier","decline"));
        LEADS = Collections.unmodifiableMap(m);
    }

    // ── Main ─────────────────────────────────────────────────────────────────

    public static void main(String[] args) {
        loadDotEnv();
        LogBus.info("ICS Runtime Java Web Demo starting…");

        var app = Javalin.create(config -> {
            config.bundledPlugins.enableCors(cors -> cors.addRule(r -> r.anyHost()));
            // Suppress Javalin's verbose startup banner
            config.showJavalinBanner = false;
        }).start(PORT);

        // ── Routes ────────────────────────────────────────────────────────────
        app.get("/",                    WebDemo::serveIndex);
        app.get("/api/status",          WebDemo::apiStatus);
        app.get("/api/leads",           WebDemo::apiLeads);
        app.post("/api/qualify",        WebDemo::apiQualify);
        app.sse("/api/qualify/stream",  WebDemo::apiQualifyStream);
        app.get("/api/metrics",         WebDemo::apiMetrics);
        app.sse("/api/logs",            WebDemo::apiLogs);

        System.out.println("╔══════════════════════════════════════════════════════════╗");
        System.out.println("║  ICS Runtime Java Web Demo  →  http://localhost:" + PORT + "    ║");
        System.out.println("╚══════════════════════════════════════════════════════════╝");
        LogBus.ok("Server ready on port " + PORT);
    }

    // ── GET / ─────────────────────────────────────────────────────────────────

    static void serveIndex(Context ctx) throws Exception {
        // Try classpath first (fat-jar), then relative path (dev mode)
        InputStream is = WebDemo.class.getResourceAsStream("/static/index.html");
        if (is == null) {
            // Dev fallback: look for file relative to working directory
            Path devPath = Path.of("src/main/resources/static/index.html");
            if (Files.exists(devPath)) {
                ctx.html(Files.readString(devPath));
                return;
            }
            ctx.status(404).result("index.html not found");
            return;
        }
        ctx.html(new String(is.readAllBytes(), StandardCharsets.UTF_8));
    }

    // ── GET /api/status ───────────────────────────────────────────────────────

    static void apiStatus(Context ctx) throws Exception {
        boolean anthropicKey = !System.getenv().getOrDefault("ANTHROPIC_API_KEY","").isBlank();
        boolean openaiKey    = !System.getenv().getOrDefault("OPENAI_API_KEY",   "").isBlank();

        String anthropicVer = sdkVersion("com.anthropic.client.AnthropicOkHttpClient");
        String openaiVer    = sdkVersion("com.openai.client.OpenAIOkHttpClient");

        ctx.json(Map.of(
            "anthropic_key",     anthropicKey,
            "openai_key",        openaiKey,
            "anthropic_version", anthropicVer != null ? anthropicVer : "?",
            "openai_version",    openaiVer    != null ? openaiVer    : "?",
            "runtime",           "java"
        ));
    }

    // ── GET /api/leads ────────────────────────────────────────────────────────

    static void apiLeads(Context ctx) throws Exception {
        ctx.json(Map.of("leads", new ArrayList<>(LEADS.values())));
    }

    // ── POST /api/qualify ─────────────────────────────────────────────────────

    static void apiQualify(Context ctx) throws Exception {
        @SuppressWarnings("unchecked")
        Map<String, Object> body = ctx.bodyAsClass(Map.class);

        String leadId    = (String) body.getOrDefault("lead_id",    "");
        String provider  = (String) body.getOrDefault("provider",   "anthropic");
        String customTask= (String) body.getOrDefault("custom_task","");

        if (!LEADS.containsKey(leadId) && !leadId.isBlank()) {
            ctx.status(404).json(Map.of("detail", "Lead '" + leadId + "' not found"));
            return;
        }

        Map<String, Object> out = runQualification(leadId, provider, customTask);
        ALL_RESULTS.add(out);
        ctx.json(out);
    }

    // ── GET /api/qualify/stream (SSE) ─────────────────────────────────────────

    static void apiQualifyStream(io.javalin.http.sse.SseClient client) {
        var qp = client.ctx();
        String leadId    = qp.queryParam("lead_id");
        String provider  = qp.queryParamAsClass("provider",   String.class).getOrDefault("anthropic");
        String customTask= qp.queryParamAsClass("custom_task",String.class).getOrDefault("");

        var logQ  = LogBus.subscribe();
        var alive = new AtomicBoolean(true);
        client.onClose(() -> alive.set(false));

        sendEvent(client, "start", toJson(Map.of("lead_id", leadId != null ? leadId : "")));

        // Drain any queued log entries to the SSE stream
        Runnable drainLogs = () -> {
            LogBus.LogEntry e;
            while ((e = logQ.poll()) != null && alive.get()) {
                sendEvent(client, "log", toJson(Map.of(
                    "type","log","ts",e.ts(),"level",e.level(),"msg",e.msg())));
            }
        };

        // Run qualification in a virtual thread
        String fLeadId = leadId, fProvider = provider, fTask = customTask;
        var future = CompletableFuture.supplyAsync(() ->
            runQualification(fLeadId, fProvider, fTask));

        while (!future.isDone() && alive.get()) {
            drainLogs.run();
            if (!future.isDone()) {
                try { Thread.sleep(50); } catch (InterruptedException ie) { break; }
            }
        }
        drainLogs.run();

        LogBus.unsubscribe(logQ);

        if (!alive.get()) return;

        try {
            Map<String, Object> result = future.get();
            ALL_RESULTS.add(result);
            sendEvent(client, "result", toJson(Map.of("type","result","data",result)));
        } catch (Exception e) {
            String msg = e.getCause() != null ? e.getCause().getMessage() : e.getMessage();
            sendEvent(client, "error", toJson(Map.of("type","error","message",
                    msg != null ? msg : "Unknown error")));
        }
        sendEvent(client, "done", "{\"type\":\"done\"}");
    }

    // ── GET /api/metrics ──────────────────────────────────────────────────────

    static void apiMetrics(Context ctx) throws Exception {
        List<Map<String, Object>> results = new ArrayList<>(ALL_RESULTS);
        if (results.isEmpty()) {
            ctx.json(Map.of("total_runs",0,"total_cost_usd",0,"cache_hit_rate",0,
                            "total_tokens_saved",0,"total_violations",0));
            return;
        }
        double totalCost   = results.stream().mapToDouble(r -> (double) r.getOrDefault("cost_usd", 0.0)).sum();
        long   cacheHits   = results.stream().filter(r -> Boolean.TRUE.equals(r.get("cache_hit"))).count();
        int    tokensSaved = results.stream().mapToInt(r -> (int) r.getOrDefault("tokens_saved", 0)).sum();
        int    violations  = results.stream().mapToInt(r -> {
            Object v = r.get("violations");
            return v instanceof List<?> l ? l.size() : 0;
        }).sum();
        ctx.json(Map.of(
            "total_runs",          results.size(),
            "total_cost_usd",      Math.round(totalCost * 1_000_000.0) / 1_000_000.0,
            "cache_hit_rate",      Math.round(cacheHits * 1000.0 / results.size()) / 10.0,
            "total_tokens_saved",  tokensSaved,
            "total_violations",    violations,
            "runs",                results.subList(Math.max(0, results.size() - 10), results.size())
        ));
    }

    // ── GET /api/logs (persistent SSE) ────────────────────────────────────────

    static void apiLogs(io.javalin.http.sse.SseClient client) {
        var q     = LogBus.subscribe();
        var alive = new AtomicBoolean(true);
        client.onClose(() -> alive.set(false));

        // Replay history to new subscriber
        for (LogBus.LogEntry e : LogBus.history()) {
            sendEvent(client, "message", toJson(Map.of("ts",e.ts(),"level",e.level(),"msg",e.msg())));
        }

        // Keep-alive loop
        while (alive.get()) {
            LogBus.LogEntry e;
            while ((e = q.poll()) != null && alive.get()) {
                sendEvent(client, "message", toJson(Map.of("ts",e.ts(),"level",e.level(),"msg",e.msg())));
            }
            if (alive.get()) {
                try { Thread.sleep(50); }
                catch (InterruptedException ie) { break; }
            }
        }
        LogBus.unsubscribe(q);
    }

    // ── Qualification helper ──────────────────────────────────────────────────

    private static Map<String, Object> runQualification(
            String leadId, String provider, String customTask) {

        Map<String, Object> lead = LEADS.get(leadId);
        String task = (customTask != null && !customTask.isBlank()) ? customTask :
            "Qualify lead " + leadId + ": "
            + (lead != null ? lead.get("company") : leadId)
            + ". Look up their CRM data, run the eligibility check, and provide a full "
            + "qualification decision with all required fields.";

        LogBus.info("[qualify] " + leadId + " via " + provider);

        try {
            var agent = AgentManager.get(provider);
            Map<String, Object> vars = new LinkedHashMap<>();
            if (leadId != null && !leadId.isBlank()) vars.put("lead_id", leadId);

            long t0 = System.currentTimeMillis();
            RunResult result;
            try (Session session = agent.session(vars)) {
                result = session.run(task);
            }
            long elapsed = System.currentTimeMillis() - t0;

            // Tool calls summary
            List<Map<String, Object>> toolSummary = new ArrayList<>();
            for (ToolCallRecord tc : result.getToolCalls()) {
                Map<String, Object> t = new LinkedHashMap<>();
                t.put("name",        tc.getToolName());
                t.put("input",       tc.getInput());
                t.put("output",      tc.getOutput());
                t.put("duration_ms", tc.getDurationMs());
                t.put("blocked",     tc.isBlocked());
                toolSummary.add(t);
            }

            // Violations summary
            List<Map<String, Object>> violSummary = new ArrayList<>();
            for (Violation v : result.getViolations()) {
                Map<String, Object> vm = new LinkedHashMap<>();
                vm.put("rule",     v.getRule());
                vm.put("kind",     v.getKind());
                vm.put("severity", v.getSeverity());
                vm.put("evidence", v.getEvidence() != null
                    ? v.getEvidence().substring(0, Math.min(120, v.getEvidence().length())) : "");
                violSummary.add(vm);
            }

            // Parsed result (Map from OutputContract)
            Object parsed = result.getParsed();

            Map<String, Object> out = new LinkedHashMap<>();
            out.put("lead_id",           leadId);
            out.put("provider",          result.getProvider());
            out.put("model",             result.getModel());
            out.put("session_id",        result.getSessionId());
            out.put("text",              result.getText());
            out.put("validated",         result.isValidated());
            out.put("violations",        violSummary);
            out.put("parsed",            parsed);
            out.put("cache_hit",         result.isCacheHit());
            out.put("cache_write",       result.isCacheWrite());
            out.put("tokens_saved",      result.getTokensSaved());
            out.put("input_tokens",      result.getInputTokens());
            out.put("output_tokens",     result.getOutputTokens());
            out.put("cache_write_tokens",result.getCacheWriteTokens());
            out.put("cost_usd",          result.getCostUsd());
            out.put("latency_ms",        result.getLatencyMs());
            out.put("tool_calls",        toolSummary);

            LogBus.ok("[qualify] " + leadId + " done — "
                + "cache_hit=" + result.isCacheHit()
                + " saved=" + result.getTokensSaved()
                + " violations=" + result.getViolations().size()
                + String.format(" cost=$%.4f", result.getCostUsd()));

            return out;

        } catch (Exception e) {
            LogBus.error("[qualify] " + leadId + " failed: " + e.getMessage());
            throw new RuntimeException(e.getMessage(), e);
        }
    }

    // ── Utilities ─────────────────────────────────────────────────────────────

    private static void sendEvent(io.javalin.http.sse.SseClient client,
                                  String event, String data) {
        try { client.sendEvent(event, data); }
        catch (Exception ignored) {}
    }

    private static String toJson(Object obj) {
        try { return JSON.writeValueAsString(obj); }
        catch (Exception e) { return "{}"; }
    }

    private static String sdkVersion(String className) {
        try {
            Class<?> cls = Class.forName(className);
            var pkg = cls.getPackage();
            return pkg != null && pkg.getImplementationVersion() != null
                    ? pkg.getImplementationVersion() : "present";
        } catch (ClassNotFoundException e) {
            return null;
        }
    }

    /** Load a .env file from the current directory or parent. */
    private static void loadDotEnv() {
        for (String candidate : new String[]{".env", "../.env", "../../.env"}) {
            var p = Path.of(candidate);
            if (!Files.exists(p)) continue;
            try {
                for (String raw : Files.readAllLines(p)) {
                    String line = raw.strip();
                    if (line.isEmpty() || line.startsWith("#") || !line.contains("=")) continue;
                    int eq = line.indexOf('=');
                    String key = line.substring(0, eq).strip();
                    String val = line.substring(eq + 1).strip()
                            .replaceAll("^['\"]|['\"]$","");
                    if (!key.isBlank() && System.getenv(key) == null)
                        System.setProperty(key, val);   // best-effort; env vars take priority
                }
            } catch (Exception ignored) {}
            break;
        }
    }
}
