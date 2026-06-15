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
    public List<AuditEvent> getAudit(@RequestParam(defaultValue = "100") int limit) {
        int clamped = Math.min(Math.max(limit, 1), MAX_LIMIT);
        return auditRepository.findAllByOrderByTimestampDesc(PageRequest.of(0, clamped));
    }
}
