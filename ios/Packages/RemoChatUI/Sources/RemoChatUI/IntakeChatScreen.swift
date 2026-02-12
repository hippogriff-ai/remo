import SwiftUI
import RemoModels
import RemoNetworking

/// Chat interface for the intake conversation.
/// Bubble-style messages, quick-reply chips, free-text input, progress indicator.
public struct IntakeChatScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var inputText = ""
    @State private var isSending = false
    @State private var showSkipConfirmation = false
    @State private var errorMessage: String?

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
                        .accessibilityIdentifier("chat_input")

                    Button {
                        Task { await sendMessage(inputText) }
                    } label: {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.title2)
                    }
                    .disabled(inputText.trimmingCharacters(in: .whitespaces).isEmpty || isSending)
                    .accessibilityLabel("Send message")
                    .accessibilityIdentifier("chat_send")
                }
                .padding()
            }
        }
        .navigationTitle("Design Chat")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .toolbar {
            if projectState.inspirationPhotoCount > 0 {
                ToolbarItem(placement: .primaryAction) {
                    Button("Skip") {
                        showSkipConfirmation = true
                    }
                    .font(.subheadline)
                    .accessibilityIdentifier("chat_skip")
                }
            }
        }
        .confirmationDialog("Skip Intake?", isPresented: $showSkipConfirmation, titleVisibility: .visible) {
            Button("Skip", role: .destructive) {
                Task { await skipIntake() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Skipping will use your inspiration photos without additional style preferences. This may reduce design quality.")
        }
        .task {
            if projectState.chatMessages.isEmpty { await startConversation() }
        }
        .alert("Error", isPresented: .init(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })) {
            Button("OK") { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
    }

    private var shouldShowTextInput: Bool {
        let output = projectState.currentIntakeOutput
        return output?.isOpenEnded == true || (output?.options == nil && !projectState.chatMessages.isEmpty && output?.isSummary != true)
    }

    private func startConversation() async {
        guard let projectId = projectState.projectId else {
            assertionFailure("startConversation() called without projectId")
            errorMessage = "Project not initialized"
            return
        }
        do {
            let output = try await client.startIntake(projectId: projectId, mode: "full")
            projectState.chatMessages.append(ChatMessage(role: "assistant", content: output.agentMessage))
            projectState.currentIntakeOutput = output
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func sendMessage(_ message: String) async {
        guard !isSending else { return }
        guard let projectId = projectState.projectId else {
            assertionFailure("sendMessage() called without projectId")
            errorMessage = "Project not initialized"
            return
        }
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
            errorMessage = error.localizedDescription
        }
    }

    private func confirmBrief(_ brief: DesignBrief) async {
        guard let projectId = projectState.projectId else {
            assertionFailure("confirmBrief() called without projectId")
            errorMessage = "Project not initialized"
            return
        }
        do {
            try await client.confirmIntake(projectId: projectId, brief: brief)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func skipIntake() async {
        guard let projectId = projectState.projectId else {
            assertionFailure("skipIntake() called without projectId")
            errorMessage = "Project not initialized"
            return
        }
        do {
            try await client.skipIntake(projectId: projectId)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            errorMessage = error.localizedDescription
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
                .background(isUser ? Color.accentColor : Color.secondary.opacity(0.2))
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
                    .background(Color.secondary.opacity(0.12))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Option \(option.number): \(option.label)")
                .accessibilityIdentifier("chat_option_\(option.number)")
            }
        }
    }
}

// MARK: - Preview

#Preview {
    NavigationStack {
        IntakeChatScreen(projectState: .preview(step: .intake), client: MockWorkflowClient(delay: .zero))
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
            .accessibilityIdentifier("chat_confirm_brief")
        }
        .padding()
        .background(Color.secondary.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }
}
