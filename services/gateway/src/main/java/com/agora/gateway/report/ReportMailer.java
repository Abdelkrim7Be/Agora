package com.agora.gateway.report;

import com.agora.gateway.config.GatewayProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSenderImpl;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Properties;

/**
 * Notifies admins by mail when a report comes in, same best-effort contract as
 * {@link com.agora.gateway.user.InvitationMailer}: no SMTP relay configured is a
 * first-class case, not an error — the report is already persisted and visible
 * on the admin Reports page either way.
 */
@Component
public class ReportMailer {

    private static final Logger log = LoggerFactory.getLogger(ReportMailer.class);

    private final GatewayProperties properties;

    public ReportMailer(GatewayProperties properties) {
        this.properties = properties;
    }

    public boolean smtpConfigured() {
        return properties.getSmtp().isConfigured();
    }

    /** Best effort, one admin at a time — a relay hiccup on one address must not skip the rest. */
    public void notifyAdmins(List<String> adminEmails, PlatformReport report) {
        if (!smtpConfigured() || adminEmails == null || adminEmails.isEmpty()) return;
        GatewayProperties.Smtp smtp = properties.getSmtp();
        for (String to : adminEmails) {
            if (to == null || to.isBlank()) continue;
            SimpleMailMessage message = new SimpleMailMessage();
            message.setFrom(smtp.getFrom());
            message.setTo(to);
            message.setSubject("[Agora] Nouveau signalement : " + report.getSubject());
            message.setText("""
                    Un nouveau signalement a été soumis par %s (%s).

                    Gravité déclarée : %s

                    %s

                    Ouvrez la page Signalements de la plateforme pour le traiter.
                    """.formatted(
                    report.getReporterUsername(),
                    report.getReporterRole() == null ? "rôle inconnu" : report.getReporterRole(),
                    report.getSeverity(),
                    report.getDescription()
            ));
            try {
                sender(smtp).send(message);
            } catch (Exception e) {
                log.warn("report notification mail to {} could not be sent: {}", to, e.getMessage());
            }
        }
    }

    private JavaMailSenderImpl sender(GatewayProperties.Smtp smtp) {
        JavaMailSenderImpl sender = new JavaMailSenderImpl();
        sender.setHost(smtp.getHost());
        sender.setPort(smtp.getPort());
        sender.setUsername(smtp.getUsername());
        sender.setPassword(smtp.getPassword());
        Properties mailProperties = sender.getJavaMailProperties();
        mailProperties.put("mail.smtp.auth", String.valueOf(!smtp.getUsername().isBlank()));
        mailProperties.put("mail.smtp.starttls.enable", "true");
        mailProperties.put("mail.smtp.timeout", "10000");
        mailProperties.put("mail.smtp.connectiontimeout", "10000");
        return sender;
    }
}
