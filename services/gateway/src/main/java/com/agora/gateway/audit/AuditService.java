package com.agora.gateway.audit;

import org.springframework.stereotype.Service;

@Service
public class AuditService {

    private final AuditRepository repository;

    public AuditService(AuditRepository repository) {
        this.repository = repository;
    }

    public void record(String username, String role, String action, String method,
                       String path, Integer upstreamStatus, String outcome) {
        repository.save(new AuditEvent(username, role, action, method, path, upstreamStatus, outcome));
    }
}
