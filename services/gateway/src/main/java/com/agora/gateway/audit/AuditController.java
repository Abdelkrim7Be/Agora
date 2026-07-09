package com.agora.gateway.audit;

import org.springframework.data.domain.PageRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
public class AuditController {

    private static final int MAX_LIMIT = 500;

    private final AuditRepository auditRepository;

    public AuditController(AuditRepository auditRepository) {
        this.auditRepository = auditRepository;
    }

    @GetMapping("/audit")
    public List<AuditEvent> getAudit(
            @RequestParam(defaultValue = "100") int limit,
            @RequestParam(defaultValue = "0") int page) {
        int clampedLimit = Math.min(Math.max(limit, 1), MAX_LIMIT);
        int clampedPage = Math.max(page, 0);
        return auditRepository.findAllByOrderByTimestampDesc(PageRequest.of(clampedPage, clampedLimit));
    }
}
