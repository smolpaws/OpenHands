import { describe, expect, it } from "vitest";
import { CANVAS_UI_CLIENT_TOOL_NAME } from "#/constants/canvas-ui";
import { LAUNCH_CHILD_CONVERSATION_TOOL_NAME } from "#/constants/child-conversation";
import {
  SMOLPAWS_MEMORY_TOOL_MODULE,
  SMOLPAWS_MEMORY_TOOL_NAME,
} from "#/constants/smolpaws-memory";
import { DEFAULT_SETTINGS } from "#/services/settings";
import type { Settings } from "#/types/settings";
import {
  AGENT_CANVAS_SOURCE,
  CLIENT_SOURCE_TAG_KEY,
  buildStartConversationRequest,
} from "./agent-server-adapter";

const encryptedValue = "gAAAAAencrypted-mcp-header";

function makeSettings(agentSettings: Settings["agent_settings"]): Settings {
  return {
    ...DEFAULT_SETTINGS,
    agent_settings: agentSettings,
    conversation_settings: {
      confirmation_mode: false,
      security_analyzer: null,
      max_iterations: 20,
    },
  };
}

function getAgentContextSkillNames(
  payload: ReturnType<typeof buildStartConversationRequest>,
): Array<string | undefined> {
  const agentSettings = payload.agent_settings as
    | {
        agent_context?: {
          skills?: Array<{ name?: string }>;
        };
      }
    | undefined;
  return agentSettings?.agent_context?.skills?.map((skill) => skill.name) ?? [];
}

describe("buildStartConversationRequest", () => {
  it("marks OpenHands start requests as encrypted when MCP headers are encrypted", () => {
    const agentSettings = {
      agent_kind: "openhands",
      llm: {
        model: "litellm_proxy/openai/gpt-5.5",
        api_key: "gAAAAAencrypted-llm-api-key",
      },
      mcp_config: {
        linear: {
          url: "https://mcp.linear.app/mcp",
          transport: "http",
          headers: {
            Authorization: encryptedValue,
          },
        },
      },
    };
    const settings = makeSettings(agentSettings);

    const payload = buildStartConversationRequest({
      settings,
      encryptedAgentSettings: agentSettings,
      encryptedConversationSettings: settings.conversation_settings!,
      secretsEncrypted: true,
    });

    expect(payload.agent_settings!.agent_kind).toBe("openhands");
    expect(payload.agent_settings!.mcp_config).toEqual(
      agentSettings.mcp_config,
    );
    expect(payload.secrets_encrypted).toBe(true);
  });

  it("marks ACP start requests as encrypted when MCP headers are encrypted", () => {
    const agentSettings = {
      agent_kind: "acp",
      acp_server: "codex",
      acp_command: ["codex-acp"],
      acp_model: "gpt-5.5/medium",
      mcp_config: {
        linear: {
          url: "https://mcp.linear.app/mcp",
          transport: "http",
          headers: {
            Authorization: encryptedValue,
          },
        },
      },
    };
    const settings = makeSettings(agentSettings);

    const payload = buildStartConversationRequest({
      settings,
      encryptedAgentSettings: agentSettings,
      encryptedConversationSettings: settings.conversation_settings!,
      secretsEncrypted: true,
    });

    expect(payload.agent_settings!.agent_kind).toBe("acp");
    expect(payload.agent_settings!.mcp_config).toEqual(
      agentSettings.mcp_config,
    );
    expect(payload.secrets_encrypted).toBe(true);
  });

  it("keeps ACP start requests unencrypted when no encrypted MCP values are present", () => {
    const agentSettings = {
      agent_kind: "acp",
      acp_server: "codex",
      acp_command: ["codex-acp"],
      acp_model: "gpt-5.5/medium",
      mcp_config: {
        publicDocs: {
          url: "https://docs.example.com/mcp",
          transport: "http",
        },
      },
    };
    const settings = makeSettings(agentSettings);

    const payload = buildStartConversationRequest({
      settings,
      encryptedAgentSettings: agentSettings,
      encryptedConversationSettings: settings.conversation_settings!,
      secretsEncrypted: true,
    });

    expect(payload.agent_settings!.agent_kind).toBe("acp");
    expect(payload.secrets_encrypted).toBeUndefined();
  });

  it("excludes disabled skills from OpenHands conversation context", () => {
    const settings = makeSettings({
      agent_kind: "openhands",
      llm: {
        model: "litellm_proxy/openai/gpt-5.5",
        api_key: "sk-test",
      },
      agent_context: {
        skills: [
          { name: "disabled-custom", content: "disabled" },
          { name: "enabled-custom", content: "enabled" },
        ],
      },
    });
    settings.disabled_skills = ["agent-memory", "disabled-custom"];

    const payload = buildStartConversationRequest({ settings });
    const skillNames = getAgentContextSkillNames(payload);

    expect(skillNames).not.toContain("agent-memory");
    expect(skillNames).not.toContain("disabled-custom");
    expect(skillNames).toContain("enabled-custom");
    expect(skillNames).toContain("add-javadoc");

    expect(payload.agent_settings?.agent_context?.disabled_skills).toEqual([
      "agent-memory",
      "disabled-custom",
    ]);
  });

  it("excludes disabled skills from ACP conversation context", () => {
    const settings = makeSettings({
      agent_kind: "acp",
      acp_server: "codex",
      acp_command: ["codex-acp"],
      acp_model: "gpt-5.5/medium",
      agent_context: {
        skills: [
          { name: "disabled-custom", content: "disabled" },
          { name: "enabled-custom", content: "enabled" },
        ],
      },
    });
    settings.disabled_skills = ["agent-memory", "disabled-custom"];

    const payload = buildStartConversationRequest({ settings });
    const skillNames = getAgentContextSkillNames(payload);

    expect(skillNames).not.toContain("agent-memory");
    expect(skillNames).not.toContain("disabled-custom");
    expect(skillNames).toContain("enabled-custom");
    expect(skillNames).toContain("add-javadoc");

    expect(payload.agent_settings?.agent_context?.disabled_skills).toEqual([
      "agent-memory",
      "disabled-custom",
    ]);
  });
});

describe("buildStartConversationRequest — agentProfileId path", () => {
  it("sends agent_profile_id and omits agent_settings (mutually exclusive)", () => {
    const settings = makeSettings({
      agent_kind: "openhands",
      llm: { model: "litellm_proxy/openai/gpt-5.5", api_key: "sk-test" },
    });

    const payload = buildStartConversationRequest({
      settings,
      agentProfileId: "profile-xyz",
      agentProfileKind: "openhands",
    });

    expect(payload.agent_profile_id).toBe("profile-xyz");
    expect(payload.agent_settings).toBeUndefined();
    expect(payload.client_tools.map((tool) => tool.name)).toEqual([
      CANVAS_UI_CLIENT_TOOL_NAME,
      LAUNCH_CHILD_CONVERSATION_TOOL_NAME,
    ]);
  });

  it("suppresses the ACP server tag when launching from a profile", () => {
    const agentSettings = {
      agent_kind: "acp",
      acp_server: "codex",
      acp_command: ["codex-acp"],
      acp_model: "gpt-5.5/medium",
    };

    // Without a profile the ACP server tag is stamped from settings...
    expect(
      buildStartConversationRequest({ settings: makeSettings(agentSettings) })
        .tags,
    ).toBeDefined();

    // ...but a profile launch resolves the server server-side, so the tag
    // (which may not match the launched profile) is omitted while the client
    // source telemetry tag is still stamped.
    const payload = buildStartConversationRequest({
      settings: makeSettings(agentSettings),
      agentProfileId: "profile-xyz",
    });
    expect(payload.tags).toEqual({
      [CLIENT_SOURCE_TAG_KEY]: AGENT_CANVAS_SOURCE,
    });
  });

  it("merges caller extraTags with the client-source tag", () => {
    const payload = buildStartConversationRequest({
      settings: makeSettings({}),
      extraTags: { smolpaws: "insider" },
    });
    expect(payload.tags).toEqual({
      smolpaws: "insider",
      [CLIENT_SOURCE_TAG_KEY]: AGENT_CANVAS_SOURCE,
    });
  });

  it("does not let extraTags override the reserved client-source tag", () => {
    const payload = buildStartConversationRequest({
      settings: makeSettings({}),
      extraTags: { [CLIENT_SOURCE_TAG_KEY]: "spoofed", smolpaws: "insider" },
    });
    expect(payload.tags).toEqual({
      smolpaws: "insider",
      [CLIENT_SOURCE_TAG_KEY]: AGENT_CANVAS_SOURCE,
    });
  });

  it("injects the insider SmolPaws system suffix for smolpaws=insider", () => {
    const payload = buildStartConversationRequest({
      settings: makeSettings({ agent_kind: "openhands" }),
      extraTags: { smolpaws: "insider" },
    });
    const suffix = (
      payload.agent_settings?.agent_context as
        | { system_message_suffix?: string }
        | undefined
    )?.system_message_suffix;
    expect(suffix).toContain("SMOLPAWS_INSIDER");
    expect(suffix).toContain("LOCAL OpenHands agent-server");
    expect(suffix).toContain("OPENHANDS_API_KEY");
  });

  it("attaches the server-executed memory tool only to insider conversations", () => {
    const insider = buildStartConversationRequest({
      settings: makeSettings({ agent_kind: "openhands" }),
      extraTags: { smolpaws: "insider" },
    });
    const regular = buildStartConversationRequest({
      settings: makeSettings({ agent_kind: "openhands" }),
    });

    expect(insider.agent_settings?.tools).toContainEqual({
      name: SMOLPAWS_MEMORY_TOOL_NAME,
      params: {},
    });
    expect(insider.tool_module_qualnames).toEqual({
      [SMOLPAWS_MEMORY_TOOL_NAME]: SMOLPAWS_MEMORY_TOOL_MODULE,
    });
    expect(regular.agent_settings?.tools).not.toContainEqual({
      name: SMOLPAWS_MEMORY_TOOL_NAME,
      params: {},
    });
    expect(regular.tool_module_qualnames).toBeUndefined();
  });

  it("strips settings-provided copies of the reserved memory capability", () => {
    const settings = makeSettings({
      agent_kind: "openhands",
      tools: [{ name: SMOLPAWS_MEMORY_TOOL_NAME, params: { root: "/tmp" } }],
    });
    settings.conversation_settings = {
      ...settings.conversation_settings,
      tool_module_qualnames: {
        [SMOLPAWS_MEMORY_TOOL_NAME]: "untrusted_memory_module",
      },
    };

    const payload = buildStartConversationRequest({ settings });

    expect(payload.agent_settings?.tools).not.toContainEqual(
      expect.objectContaining({ name: SMOLPAWS_MEMORY_TOOL_NAME }),
    );
    expect(payload.tool_module_qualnames).toBeUndefined();
  });

  it("does not inject insider memory capabilities for non-insider conversations", () => {
    const payload = buildStartConversationRequest({
      settings: makeSettings({ agent_kind: "openhands" }),
    });
    const suffix = (
      payload.agent_settings?.agent_context as
        | { system_message_suffix?: string }
        | undefined
    )?.system_message_suffix;
    expect(suffix ?? "").not.toContain("SMOLPAWS_INSIDER");
    expect(suffix ?? "").not.toContain("smolpaws_memory");
  });

  it("suppresses secrets_encrypted when launching from a profile", () => {
    const agentSettings = {
      agent_kind: "openhands",
      llm: {
        model: "litellm_proxy/openai/gpt-5.5",
        api_key: "gAAAAAencrypted-llm-api-key",
      },
      mcp_config: {
        mcpServers: {
          linear: {
            url: "https://mcp.linear.app/mcp",
            transport: "http",
            headers: { Authorization: encryptedValue },
          },
        },
      },
    };
    const settings = makeSettings(agentSettings);

    // Same inputs without a profile would set secrets_encrypted (covered
    // above); the profile path defers secret resolution to the server.
    const payload = buildStartConversationRequest({
      settings,
      encryptedAgentSettings: agentSettings,
      encryptedConversationSettings: settings.conversation_settings!,
      secretsEncrypted: true,
      agentProfileId: "profile-xyz",
    });

    expect(payload.secrets_encrypted).toBeUndefined();
  });
});
