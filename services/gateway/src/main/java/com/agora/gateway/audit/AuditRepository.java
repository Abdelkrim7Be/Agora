package com.agora.gateway.audit;

import org.springframework.data.domain.Pageable;
import org.springframework.data.repository.Repository;

import java.time.Instant;
import java.util.List;

// Deliberately extends the minimal Repository (not JpaRepository) so the audit log
// exposes only append + read — no delete/update surface. Keeps the trail tamper-proof.
public interface AuditRepository extends Repository<AuditEvent, Long> {
    AuditEvent save(AuditEvent event);

    List<AuditEvent> findAll();

    List<AuditEvent> findAllByOrderByTimestampDesc(Pageable pageable);

    long countByUsernameIgnoreCaseAndActionAndOutcomeAndTimestampAfter(
            String username,
            String action,
            String outcome,
            Instant cutoff
    );

    long countByActionAndOutcomeAndTimestampAfter(
            String action,
            String outcome,
            Instant cutoff
    );
}
