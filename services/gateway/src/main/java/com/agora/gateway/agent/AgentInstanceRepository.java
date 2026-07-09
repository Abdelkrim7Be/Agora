package com.agora.gateway.agent;

import org.springframework.data.jpa.repository.JpaRepository;

public interface AgentInstanceRepository extends JpaRepository<AgentInstance, String> {
}
