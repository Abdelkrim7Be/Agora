package com.agora.gateway.health;

import com.agora.gateway.agent.AgentInstance;
import com.agora.gateway.agent.AgentRegistryService;
import com.agora.gateway.user.UserRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Fans out to every agent instance's health + DLQ, plus the gateway's own DB
 * reachability, and rolls it up into one verdict an admin can read at a
 * glance: ok / warn / critical. This is the thing {@code HealthPage} cannot
 * answer — that page only ever looks at whichever single instance happens to
 * be selected.
 */
@Service
public class PlatformAuditService {

    private static final List<String> VERDICT_ORDER = List.of("ok", "warn", "critical");
    private static final List<String> CRITICAL_COMPONENTS = List.of("agent", "database", "security", "redis");

    private final AgentRegistryService agentRegistryService;
    private final UserRepository users;
    private final PlatformAuditRepository runs;
    private final ObjectMapper objectMapper;

    public PlatformAuditService(AgentRegistryService agentRegistryService, UserRepository users,
                                PlatformAuditRepository runs, ObjectMapper objectMapper) {
        this.agentRegistryService = agentRegistryService;
        this.users = users;
        this.runs = runs;
        this.objectMapper = objectMapper;
    }

    public record AuditRunResult(
            Long id, String ranAt, String ranBy, String verdict, int instanceCount,
            int warningCount, List<Map<String, Object>> instances, String gatewayVerdict, String gatewayNote
    ) {}

    public AuditRunResult run(String actor) {
        List<AgentInstance> instances = agentRegistryService.allInstances();
        List<Map<String, Object>> instanceRows = new ArrayList<>();
        String worst = "ok";
        int warningCount = 0;

        for (AgentInstance instance : instances) {
            AgentRegistryService.InstanceAuditResult probe = agentRegistryService.auditProbe(instance);
            String verdict = instanceVerdict(probe);
            worst = worse(worst, verdict);
            warningCount += probe.warnings().size();

            Map<String, Object> row = new LinkedHashMap<>();
            row.put("instanceId", probe.instanceId());
            row.put("displayName", probe.displayName());
            row.put("reachable", probe.reachable());
            row.put("verdict", verdict);
            row.put("components", probe.componentStatuses());
            row.put("dlqPending", probe.dlqPendingCount());
            row.put("warnings", probe.warnings());
            instanceRows.add(row);
        }

        String gatewayVerdict = "ok";
        String gatewayNote = "database reachable";
        try {
            users.count();
        } catch (RuntimeException ex) {
            gatewayVerdict = "critical";
            gatewayNote = "gateway database unreachable: " + (ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage());
        }
        worst = worse(worst, gatewayVerdict);

        String detailsJson;
        try {
            Map<String, Object> details = new LinkedHashMap<>();
            details.put("instances", instanceRows);
            details.put("gatewayVerdict", gatewayVerdict);
            details.put("gatewayNote", gatewayNote);
            detailsJson = objectMapper.writeValueAsString(details);
        } catch (Exception ex) {
            detailsJson = "{}";
        }

        PlatformAuditRun saved = runs.save(new PlatformAuditRun(actor, worst, instances.size(), warningCount, detailsJson));
        return new AuditRunResult(saved.getId(), saved.getRanAt().toString(), saved.getRanBy(), saved.getVerdict(),
                saved.getInstanceCount(), saved.getWarningCount(), instanceRows, gatewayVerdict, gatewayNote);
    }

    public List<PlatformAuditRun> history() {
        return runs.findTop20ByOrderByRanAtDesc();
    }

    public java.util.Optional<PlatformAuditRun> find(Long id) {
        return runs.findById(id);
    }

    private String instanceVerdict(AgentRegistryService.InstanceAuditResult probe) {
        if (!probe.reachable()) return "critical";
        for (String key : CRITICAL_COMPONENTS) {
            if ("down".equals(probe.componentStatuses().get(key))) return "critical";
        }
        if (probe.dlqPendingCount() > 0) return "warn";
        String poller = probe.componentStatuses().get("poller");
        if ("down".equals(poller) || "paused".equals(poller)) return "warn";
        return probe.warnings().isEmpty() ? "ok" : "warn";
    }

    private String worse(String a, String b) {
        return VERDICT_ORDER.indexOf(a) >= VERDICT_ORDER.indexOf(b) ? a : b;
    }
}
