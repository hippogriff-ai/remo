import SwiftUI
import RemoModels

/// Chat interface for the intake conversation.
/// Bubble-style messages, quick-reply chips, free-text input, progress indicator.
public struct IntakeChatScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var inputText = ""
    @State private var isSending = false
    @State private var hasStarted = false

    public init(projectState: ProjectState, client: any WorkflowClientProtocol) {
        self.projectState = projectState
        self.client = client
    }

    public var body: some View {
        VStack(spacing: 0) {
            // Progress bar
            if let progress = projectState.currentIntakeOutput?.progress {
                HStack {
                    Text(progress)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                }
                .padding(.horizontal)
                .padding(.vertical, 8)
                .background(.bar)
            }

            // Messages
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 12) {
                        ForEach(Array(projectState.chatMessages.enumerated()), id: \.offset) { index, message in
                            ChatBubble(message: message)
                                .id(index)
                        }

                        // Quick reply chips
                        if let options = projectState.currentIntakeOutput?.options, !options.isEmpty {
                            QuickReplyChips(options: options) { option in
                                Task { await sendMessage(option.value) }
                            }
                        }

                        // Summary card
                        if projectState.currentIntakeOutput?.isSummary == true,
                           let brief = projectState.currentIntakeOutput?.partialBrief {
                            SummaryCard(brief: brief) {
                                Task { await confirmBrief(brief) }
                            }
                        }
                    }
                    .padding()
                }
                .onChange(of: projectState.chatMessages.count) { _, _ in
                    withAnimation {
                        proxy.scrollTo(projectState.chatMessages.count - 1, anchor: .bottom)
                    }
                }
            }

            Divider()

            // Input bar (shown when is_open_ended or no options)
            if shouldShowTextInput {
                HStack(spacing: 8) {
                    TextField("Type your message...", text: $inputText, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(1...4)

                    Button {
                        Task { await sendMessage(inputText) }
                    } label: {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.title2)
                    }
                    .disabled(inputText.trimmingCharacters(in: .whitespaces).isEmpty || isSending)
                }
                .padding()
            }
        }
        .navigationTitle("Design Chat")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Skip") {
                    Task { await skipIntake() }
                }
                .font(.subheadline)
            }
        }
        .task {
            if !hasStarted { await startConversation() }
        }
    }

    private var shouldShowTextInput: Bool {
        let output = projectState.currentIntakeOutput
        return output?.isOpenEnded == true || (output?.options == nil && hasStarted && output?.isSummary != true)
    }

    private func startConversation() async {
        guard let projectId = projectState.projectId else { return }
        do {
            let output = try await client.startIntake(projectId: projectId, mode: "full")
            projectState.chatMessages.append(ChatMessage(role: "assistant", content: output.agentMessage))
            projectState.currentIntakeOutput = output
            hasStarted = true
        } catch {
            // TODO: error handling
        }
    }

    private func sendMessage(_ message: String) async {
        guard let projectId = projectState.projectId else { return }
        let trimmed = message.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }

        isSending = true
        defer { isSending = false; inputText = "" }

        projectState.chatMessages.append(ChatMessage(role: "user", content: trimmed))

        do {
            let output = try await client.sendIntakeMessage(projectId: projectId, message: trimmed)
            projectState.chatMessages.append(ChatMessage(role: "assistant", content: output.agentMessage))
            projectState.currentIntakeOutput = output
        } catch {
            // TODO: error handling
        }
    }

    private func confirmBrief(_ brief: DesignBrief) async {
        guard let projectId = projectState.projectId else { return }
        do {
            try await client.confirmIntake(projectId: projectId, brief: brief)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            // TODO: error handling
        }
    }

    private func skipIntake() async {
        guard let projectId = projectState.projectId else { return }
        do {
            try await client.skipIntake(projectId: projectId)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            // TODO: error handling
        }
    }
}

// MARK: - Chat Bubble

struct ChatBubble: View {
    let message: ChatMessage

    private var isUser: Bool { message.role == "user" }

    var body: some View {
        HStack {
            if isUser { Spacer(minLength: 60) }
            Text(message.content)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(isUser ? Color.accentColor : Color(.systemGray5))
                .foregroundStyle(isUser ? .white : .primary)
                .clipShape(RoundedRectangle(cornerRadius: 16))
            if !isUser { Spacer(minLength: 60) }
        }
    }
}

// MARK: - Quick Reply Chips

struct QuickReplyChips: View {
    let options: [QuickReplyOption]
    let onSelect: (QuickReplyOption) -> Void

    var body: some View {
        VStack(spacing: 8) {
            ForEach(options) { option in
                Button {
                    onSelect(option)
                } label: {
                    HStack {
                        Text("\(option.number)")
                            .font(.caption.bold())
                            .frame(width: 24, height: 24)
                            .background(Color.accentColor.opacity(0.15))
                            .clipShape(Circle())
                        Text(option.label)
                            .font(.subheadline)
                        Spacer()
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .background(Color(.systemGray6))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(.plain)
            }
        }
    }
}

// MARK: - Summary Card

struct SummaryCard: View {
    let brief: DesignBrief
    let onConfirm: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Design Brief", systemImage: "doc.text")
                .font(.headline)

            LabeledContent("Room Type", value: brief.roomType)

            if !brief.painPoints.isEmpty {
                LabeledContent("Change", value: brief.painPoints.joined(separator: ", "))
            }

            if !brief.keepItems.isEmpty {
                LabeledContent("Keep", value: brief.keepItems.joined(separator: ", "))
            }

            Button("Looks Good!") {
                onConfirm()
            }
            .buttonStyle(.borderedProminent)
            .frame(maxWidth: .infinity)
        }
        .padding()
        .background(Color(.systemGray6))
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }
}
