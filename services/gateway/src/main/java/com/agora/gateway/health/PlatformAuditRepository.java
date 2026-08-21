package com.agora.gateway.health;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface PlatformAuditRepository extends JpaRepository<PlatformAuditRun, Long> {
    List<PlatformAuditRun> findTop20ByOrderByRanAtDesc();
}
