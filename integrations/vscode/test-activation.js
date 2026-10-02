const assert = require('node:assert/strict');
const Module = require('node:module');
const manifest = require('./package.json');

const originalLoad = Module._load;
const participants = [];
const providers = [];
const tools = [];
const sessionRegistrations = [];
const sidebarRegistrations = [];
Module._load = function load(request, parent, isMain) {
  if (request === 'vscode') {
    class EventEmitter {
      constructor() { this.event = () => ({dispose(){}}); }
      fire() {}
      dispose() {}
    }
    const Uri = {parse: (value) => ({toString: () => value})};
    return {
      commands: {
        registerCommand: () => ({dispose() {}}),
        executeCommand: async () => undefined,
      },
      window: {
        activeTextEditor: undefined,
        registerWebviewViewProvider: (id, provider) => {
          sidebarRegistrations.push({id, provider});
          return {dispose() {}};
        },
      },
      chat: {
        createChatParticipant: (id, handler) => {
          const participant = {dispose() {}};
          participants.push({id, handler, participant});
          return participant;
        },
        registerChatSessionItemProvider: (type, provider) => {
          sessionRegistrations.push({kind: 'items', type, provider});
          return {dispose() {}};
        },
        registerChatSessionContentProvider: (scheme, provider, participant) => {
          sessionRegistrations.push({kind: 'content', scheme, provider, participant});
          return {dispose() {}};
        },
      },
      lm: {
        registerLanguageModelChatProvider: (vendor, provider) => {
          providers.push({vendor, provider});
          return {dispose() {}};
        },
        registerTool: (name, tool) => {
          tools.push({name, tool});
          return {dispose() {}};
        },
      },
      LanguageModelChatMessageRole: {User: 1, Assistant: 2},
      ChatSessionStatus: {Completed: 1},
      LanguageModelTextPart: class LanguageModelTextPart {
        constructor(value) { this.value = value; }
      },
      LanguageModelToolResult: class LanguageModelToolResult {
        constructor(content) { this.content = content; }
      },
      EventEmitter,
      Uri,
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

try {
  const extension = require('./extension');
  extension.activate({subscriptions: []});
  assert.equal(participants.length, 1);
  assert.equal(participants[0].id, 'ia-local-do-zero.chat');
  assert.equal(typeof participants[0].handler, 'function');
  assert.equal(typeof participants[0].participant.followupProvider.provideFollowups, 'function');
  assert.equal(participants[0].participant.followupProvider.provideFollowups({metadata: {command: 'review'}}, {}, {}).length, 2);
  assert.equal(providers.length, 1);
  assert.equal(providers[0].vendor, 'ia-local');
  assert.deepEqual(manifest.contributes.languageModelChatProviders, [
    {vendor: 'ia-local', displayName: 'Brasa'},
  ]);
  assert.equal(typeof providers[0].provider.provideLanguageModelChatInformation, 'function');
  assert.equal(typeof providers[0].provider.provideLanguageModelChatResponse, 'function');
  assert.equal(typeof providers[0].provider.provideTokenCount, 'function');
  assert.equal(tools.length, 1);
  assert.equal(tools[0].name, 'iaLocal_inspectWorkspace');
  assert.equal(typeof tools[0].tool.invoke, 'function');
  assert.equal(manifest.contributes.languageModelTools[0].toolReferenceName, 'ia-local-workspace');
  assert.equal(manifest.contributes.chatParticipants[0].disambiguation.length, 1);
  assert.equal(manifest.contributes.chatSessions, undefined);
  assert.equal(manifest.enabledApiProposals, undefined);
  assert.equal(manifest.contributes.viewsContainers.secondarySidebar[0].id, 'ia-local-container');
  assert.equal(manifest.contributes.views['ia-local-container'][0].id, 'ia-local.sidebar');
  assert.equal(sidebarRegistrations.length, 1);
  assert.equal(sidebarRegistrations[0].id, 'ia-local.sidebar');
  assert.equal(sessionRegistrations.length, 0);
  console.log('chat-participant-and-model-provider-activation=ok');
} finally {
  Module._load = originalLoad;
}
