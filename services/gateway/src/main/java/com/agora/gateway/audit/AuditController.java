package com.agora.gateway.audit;

import org.springframework.data.domain.PageRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

@RestController
public class AuditController {

    private static final int MAX_LIMIT = 500;
    private static final Set<String> TECHNICAL_NOISE_PATHS = Set.of(
            "/api/agent/runs",
            "/api/agent/health",
            "/api/agent/analytics",
            "/api/agent/metrics",
            "/api/agent/sync/status",
            "/api/agent/instance-setup",
            "/api/agent/events",
            "/api/agent/notifications",
            "/api/agent/notifications/unread-count"
    );

    private final AuditRepository auditRepository;
    private final AuditService auditService;

    public AuditController(AuditRepository auditRepository, AuditService auditService) {
        this.auditRepository = auditRepository;
        this.auditService = auditService;
    }

    @GetMapping("/audit")
    public List<AuditEvent> getAudit(
            @RequestParam(defaultValue = "100") int limit,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "false") boolean includeTechnicalNoise) {
        int clampedLimit = Math.min(Math.max(limit, 1), MAX_LIMIT);
        int clampedPage = Math.max(page, 0);
        if (includeTechnicalNoise) {
            return auditRepository.findAllByOrderByTimestampDesc(PageRequest.of(clampedPage, clampedLimit));
        }
        int skip = clampedPage * clampedLimit;
        int target = skip + clampedLimit;
        int sourcePage = 0;
        int batchSize = Math.max(100, clampedLimit);
        List<AuditEvent> visible = new ArrayList<>();
        while (visible.size() < target) {
            List<AuditEvent> batch = auditRepository.findAllByOrderByTimestampDesc(PageRequest.of(sourcePage, batchSize));
            if (batch.isEmpty()) break;
            for (AuditEvent event : batch) {
                if (!isTechnicalNoise(event)) {
                    visible.add(event);
                }
            }
            if (batch.size() < batchSize) break;
            sourcePage++;
        }
        if (skip >= visible.size()) return List.of();
        return visible.subList(skip, Math.min(target, visible.size()));
    }

    private boolean isTechnicalNoise(AuditEvent event) {
        return "GET".equalsIgnoreCase(event.getMethod())
                && TECHNICAL_NOISE_PATHS.contains(normalizedPath(event.getPath()));
    }

    private String normalizedPath(String path) {
        if (path == null) return "";
        int queryStart = path.indexOf('?');
        return queryStart >= 0 ? path.substring(0, queryStart) : path;
    }

    @GetMapping("/audit/verify")
    public AuditService.ChainVerification verifyAudit() {
        return auditService.verifyChain();
    }
}
