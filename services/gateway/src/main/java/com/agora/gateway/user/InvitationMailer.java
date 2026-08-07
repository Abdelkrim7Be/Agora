package com.agora.gateway.user;

import com.agora.gateway.config.GatewayProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSenderImpl;
import org.springframework.stereotype.Component;

import java.util.Properties;

/**
 * Delivers invitation links by SMTP, when SMTP is configured.
 *
 * Deployments without a mail relay are a first-class case, not an error: the
 * endpoints return the setup link so an admin can pass it on by hand. That
 * keeps invitations usable on day one without making mail infrastructure a
 * prerequisite for onboarding.
 */
@Component
public class InvitationMailer {

    private static final Logger log = LoggerFactory.getLogger(InvitationMailer.class);

    private final GatewayProperties properties;

    public InvitationMailer(GatewayProperties properties) {
        this.properties = properties;
    }

    public boolean smtpConfigured() {
        return properties.getSmtp().isConfigured();
    }

    /**
     * Best effort. A relay that is down must not lose the invitation — the
     * token is already persisted and the link is returned to the caller either
     * way, so a failure here degrades to "deliver it yourself".
     *
     * @return true when the message was handed to the relay
     */
    public boolean send(String to, String username, String setupLink) {
        if (!smtpConfigured() || to == null || to.isBlank()) return false;

        GatewayProperties.Smtp smtp = properties.getSmtp();
        SimpleMailMessage message = new SimpleMailMessage();
        message.setFrom(smtp.getFrom());
        message.setTo(to);
        message.setSubject("Votre accès Agora");
        message.setText("""
                Bonjour %s,

                Un accès Agora a été créé pour vous. Choisissez votre mot de passe ici :

                %s

                Ce lien expire dans %d heures et ne peut être utilisé qu'une seule fois.
                Si vous n'attendiez pas cet e-mail, ignorez-le.
                """.formatted(username, setupLink, properties.getInviteExpiryHours()));

        try {
            sender(smtp).send(message);
            return true;
        } catch (Exception e) {
            // Never log the link: it is a bearer credential for the account.
            log.warn("invitation mail to {} could not be sent: {}", to, e.getMessage());
            return false;
        }
    }

    /**
     * Password reset link.
     *
     * Separate wording from an invitation because the recipient already has an
     * account: an "an access has been created for you" mail for a reset reads as
     * a sign that someone else got in. It also says what to do if they did not
     * ask, which is the only warning a person gets that someone is trying their
     * address.
     *
     * @return true when the message was handed to the relay
     */
    public boolean sendPasswordReset(String to, String username, String resetLink) {
        if (!smtpConfigured() || to == null || to.isBlank()) return false;

        GatewayProperties.Smtp smtp = properties.getSmtp();
        SimpleMailMessage message = new SimpleMailMessage();
        message.setFrom(smtp.getFrom());
        message.setTo(to);
        message.setSubject("Réinitialisation de votre mot de passe Agora");
        message.setText("""
                Bonjour %s,

                Une réinitialisation de mot de passe a été demandée pour votre compte.
                Choisissez un nouveau mot de passe ici :

                %s

                Ce lien expire dans %d heures et ne peut être utilisé qu'une seule fois.

                Si vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail :
                votre mot de passe actuel reste valable et rien n'a changé.
                """.formatted(username, resetLink, properties.getInviteExpiryHours()));

        try {
            sender(smtp).send(message);
            return true;
        } catch (Exception e) {
            // Never log the link: it is a bearer credential for the account.
            log.warn("password reset mail to {} could not be sent: {}", to, e.getMessage());
            return false;
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
