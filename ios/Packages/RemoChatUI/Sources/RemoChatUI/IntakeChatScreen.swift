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
    @State private var selectedMode: String?
    @State private var showSkipConfirmation = false
    @State private var errorMessage: String?
    @State private var selectedQuickReply: Int?
    @FocusState private var isInputFocused: Bool

    public init(projectState: ProjectState, client: any WorkflowClientProtocol) {
        self.projectState = projectState
        self.client = client
    }

    public var body: some View {
        VStack(spacing: 0) {
            if selectedMode == nil && projectState.chatMessages.isEmpty {
                modeSelectionView
            } else {
                chatView
            }
        }
        .navigationTitle("Design Chat")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .confirmationDialog("Skip Intake?", isPresented: $showSkipConfirmation, titleVisibility: .visible) {
            Button("Skip", role: .destructive) {
                Task { await skipIntake() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The intake helps Remo understand your style and needs. Designs are significantly better with it. Skip anyway?")
        }
        .alert("Error", isPresented: .init(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })) {
            Button("OK") { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
    }

    // MARK: - Mode Selection

    private var modeSelectionView: some View {
        ScrollView {
            VStack(spacing: 16) {
                Text("How would you like to tell us about your style?")
                    .font(.title3.bold())
                    .multilineTextAlignment(.center)
                    .padding(.top, 24)

                ModeButton(
                    title: "Quick Intake",
                    subtitle: "~3 questions, ~2 minutes",
                    icon: "bolt.fill",
                    identifier: "mode_quick"
                ) {
                    Task { await selectMode("quick") }
                }

                ModeButton(
                    title: "Full Intake",
                    subtitle: "~10 questions, ~8 minutes",
                    icon: "list.bullet.clipboard",
                    identifier: "mode_full"
                ) {
                    Task { await selectMode("full") }
                }

                ModeButton(
                    title: "Open Conversation",
                    subtitle: "Tell us everything, take your time",
                    icon: "bubble.left.and.bubble.right",
                    identifier: "mode_open"
                ) {
                    Task { await selectMode("open") }
                }

                if projectState.inspirationPhotoCount > 0 {
                    Button {
                        showSkipConfirmation = true
                    } label: {
                        Text("Skip — jump straight to design")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    .accessibilityIdentifier("mode_skip")
                    .padding(.top, 8)
                }
                if isSending {
                    ProgressView("Starting conversation...")
                        .padding(.top, 8)
                }
            }
            .padding(.horizontal)
            .disabled(isSending)
        }
    }

    private func selectMode(_ mode: String) async {
        isSending = true
        defer { isSending = false }
        await startConversation(mode: mode)
        if errorMessage == nil {
            selectedMode = mode
        }
    }

    // MARK: - Chat View

    private var chatView: some View {
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
                        if projectState.chatMessages.isEmpty {
                            ProgressView("Starting conversation...")
                                .padding(.top, 48)
                        }
                        ForEach(Array(projectState.chatMessages.enumerated()), id: \.offset) { index, message in
                            ChatBubble(message: message)
                                .id(index)
                        }

                        // Quick reply chips — hidden once user selects one or while waiting for response
                        if let options = projectState.currentIntakeOutput?.options, !options.isEmpty,
                           selectedQuickReply == nil, !isSending,
                           projectState.currentIntakeOutput?.isSummary != true {
                            QuickReplyChips(
                                options: options,
                                selectedId: selectedQuickReply,
                                disabled: isSending
                            ) { option in
                                selectedQuickReply = option.number
                                Task { await sendMessage(option.label) }
                            }

                            // Always offer free-text escape hatch — none of the options may fit
                            Button {
                                selectedQuickReply = -1
                                isInputFocused = true
                            } label: {
                                HStack {
                                    Image(systemName: "keyboard")
                                        .frame(width: 24, height: 24)
                                    Text("Type my own answer...")
                                        .font(.subheadline)
                                    Spacer()
                                }
                                .foregroundStyle(.secondary)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 10)
                                .background(Color.secondary.opacity(0.08))
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                            }
                            .buttonStyle(.plain)
                        }

                        // Typing indicator
                        if isSending {
                            TypingIndicatorBubble()
                                .id("typing")
                        }

                        // Summary card
                        if projectState.currentIntakeOutput?.isSummary == true,
                           let brief = projectState.currentIntakeOutput?.partialBrief {
                            SummaryCard(brief: brief) { action in
                                Task {
                                    if action == .confirm {
                                        await confirmBrief(brief)
                                    } else {
                                        await sendMessage("I want to change something")
                                    }
                                }
                            }
                        }
                    }
                    .padding()
                }
                .textSelection(.enabled)
                .onChange(of: projectState.chatMessages.count) { _, _ in
                    withAnimation {
                        proxy.scrollTo(projectState.chatMessages.count - 1, anchor: .bottom)
                    }
                    // Reset quick reply selection after response arrives
                    selectedQuickReply = nil
                }
                .onChange(of: isSending) { _, sending in
                    if sending {
                        withAnimation {
                            proxy.scrollTo("typing", anchor: .bottom)
                        }
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
                        .focused($isInputFocused)
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
    }

    private var shouldShowTextInput: Bool {
        if selectedQuickReply == -1 { return true }
        let output = projectState.currentIntakeOutput
        return output?.isOpenEnded == true || (output?.options == nil && !projectState.chatMessages.isEmpty && output?.isSummary != true)
    }

    private func startConversation(mode: String) async {
        guard let projectId = projectState.projectId else {
            assertionFailure("startConversation() called without projectId")
            errorMessage = "Project not initialized"
            return
        }
        do {
            let output = try await client.startIntake(projectId: projectId, mode: mode)
            projectState.chatMessages.append(ChatMessage(role: "assistant", content: output.agentMessage))
            projectState.currentIntakeOutput = output
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Minimum interval between UI updates when streaming deltas, so text
    /// appears as a readable typing effect instead of a sudden wall of text.
    private static let minDeltaInterval: Duration = .milliseconds(30)

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
        defer { isSending = false }

        // Capture count BEFORE optimistic append so rollback can remove the
        // user message too if the request never reached the server.
        let messageCountBefore = projectState.chatMessages.count
        projectState.chatMessages.append(ChatMessage(role: "user", content: trimmed))
        isInputFocused = false
        inputText = ""

        let history = Array(projectState.chatMessages.suffix(20))

        // Declared outside `do` so the `catch` block can tell whether the
        // server already consumed the message (deltas arrived) or not.
        var receivedAnyDelta = false

        do {
            // Try streaming first for progressive text rendering
            var receivedDone = false
            var lastDeltaTime: ContinuousClock.Instant = .now
            let stream = client.streamIntakeMessage(
                projectId: projectId,
                message: trimmed,
                conversationHistory: history,
                mode: selectedMode
            )
            for try await event in stream {
                switch event {
                case .delta(let text):
                    receivedAnyDelta = true
                    if projectState.chatMessages.last?.role != "assistant"
                        || projectState.chatMessages.count == messageCountBefore {
                        projectState.chatMessages.append(
                            ChatMessage(role: "assistant", content: text)
                        )
                    } else {
                        let idx = projectState.chatMessages.count - 1
                        projectState.chatMessages[idx].content += text
                    }
                    // Throttle: ensure minimum interval between UI updates so
                    // deltas that arrive in a burst appear as gradual typing
                    let elapsed = ContinuousClock.now - lastDeltaTime
                    if elapsed < Self.minDeltaInterval {
                        try await Task.sleep(for: Self.minDeltaInterval - elapsed)
                    }
                    lastDeltaTime = .now
                case .done(let output):
                    if !receivedAnyDelta {
                        // No streaming occurred — reveal text with typewriter effect
                        await revealTextProgressively(
                            output.agentMessage,
                            messageCountBefore: messageCountBefore
                        )
                    } else if let idx = projectState.chatMessages.indices.last,
                              projectState.chatMessages[idx].role == "assistant" {
                        // Replace streamed text with final authoritative message
                        projectState.chatMessages[idx].content = output.agentMessage
                    } else {
                        projectState.chatMessages.append(
                            ChatMessage(role: "assistant", content: output.agentMessage)
                        )
                    }
                    projectState.currentIntakeOutput = output
                    receivedDone = true
                case .error(let message):
                    if receivedAnyDelta {
                        // Server consumed the prompt — keep user turn, remove partial assistant
                        let assistantStart = messageCountBefore + 1
                        if projectState.chatMessages.count > assistantStart {
                            projectState.chatMessages.removeSubrange(assistantStart...)
                        }
                    } else {
                        // Server never processed the prompt — full rollback
                        if projectState.chatMessages.count > messageCountBefore {
                            projectState.chatMessages.removeSubrange(messageCountBefore...)
                        }
                        inputText = trimmed
                        selectedQuickReply = nil
                    }
                    errorMessage = message
                    return
                }
            }
            // Only fall back to non-streaming if we never got any data from
            // the stream. If deltas arrived, the server already processed the
            // message — re-sending would duplicate it.
            if !receivedDone && !receivedAnyDelta {
                let output = try await client.sendIntakeMessage(
                    projectId: projectId,
                    message: trimmed,
                    conversationHistory: history,
                    mode: selectedMode
                )
                await revealTextProgressively(
                    output.agentMessage,
                    messageCountBefore: messageCountBefore
                )
                projectState.currentIntakeOutput = output
            }
        } catch is CancellationError {
            // View disappeared — do nothing
        } catch {
            // If the server already processed the message (deltas arrived),
            // keep the user message but remove any partial assistant content.
            // Otherwise, roll back everything including the optimistic user message.
            if receivedAnyDelta {
                // Server consumed the message — keep user turn, remove partial assistant
                let assistantStart = messageCountBefore + 1  // after user msg
                if projectState.chatMessages.count > assistantStart {
                    projectState.chatMessages.removeSubrange(assistantStart...)
                }
                errorMessage = error.localizedDescription
            } else {
                // Server never saw the message — full rollback
                if projectState.chatMessages.count > messageCountBefore {
                    projectState.chatMessages.removeSubrange(messageCountBefore...)
                }
                inputText = trimmed
                selectedQuickReply = nil

                // "wrong_step" means the workflow advanced past intake — refresh state
                if case .httpError(409, let response) = error as? APIError,
                   response.error == "wrong_step" {
                    if let newState = try? await client.getState(projectId: projectId) {
                        projectState.apply(newState)
                        return
                    }
                }
                errorMessage = error.localizedDescription
            }
        }
    }

    /// Reveal text progressively for a natural typing effect when streaming
    /// produced no deltas (e.g. non-streaming fallback or tool-use JSON delay).
    private func revealTextProgressively(
        _ text: String,
        messageCountBefore: Int
    ) async {
        if projectState.chatMessages.last?.role != "assistant"
            || projectState.chatMessages.count <= messageCountBefore {
            projectState.chatMessages.append(ChatMessage(role: "assistant", content: ""))
        }
        let idx = projectState.chatMessages.count - 1
        // Reveal ~4 characters per frame at 30ms intervals (~130 chars/sec)
        let chunkSize = 4
        var offset = text.startIndex
        while offset < text.endIndex {
            let end = text.index(offset, offsetBy: chunkSize, limitedBy: text.endIndex) ?? text.endIndex
            projectState.chatMessages[idx].content += text[offset..<end]
            offset = end
            try? await Task.sleep(for: Self.minDeltaInterval)
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
        } catch {
            // 409 means the workflow already advanced (signal was received earlier)
            // — fall through to state refresh below
            if case .httpError(409, _) = error as? APIError {
                // Signal already processed — continue to refresh
            } else {
                errorMessage = error.localizedDescription
                return
            }
        }
        // Always refresh state after confirm — the workflow may have advanced
        // through generation quickly, so we need the latest step for navigation
        do {
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            // Signal was sent; force step forward so the router navigates away
            // even if the state refresh fails
            projectState.step = .generation
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

    private var markdownContent: AttributedString {
        (try? AttributedString(markdown: message.content, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(message.content)
    }

    var body: some View {
        HStack {
            if isUser { Spacer(minLength: 60) }
            Text(markdownContent)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(isUser ? Color.accentColor : Color.secondary.opacity(0.2))
                .foregroundStyle(isUser ? .white : .primary)
                .clipShape(RoundedRectangle(cornerRadius: 16))
                #if os(iOS)
                .contextMenu {
                    Button {
                        UIPasteboard.general.string = message.content
                    } label: {
                        Label("Copy", systemImage: "doc.on.doc")
                    }
                }
                #endif
            if !isUser { Spacer(minLength: 60) }
        }
    }
}

// MARK: - Quick Reply Chips

struct QuickReplyChips: View {
    let options: [QuickReplyOption]
    var selectedId: Int?
    var disabled: Bool = false
    let onSelect: (QuickReplyOption) -> Void

    var body: some View {
        VStack(spacing: 8) {
            ForEach(options) { option in
                let isSelected = selectedId == option.number
                Button {
                    onSelect(option)
                } label: {
                    HStack {
                        if isSelected {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.caption.bold())
                                .frame(width: 24, height: 24)
                                .foregroundStyle(.white)
                        } else {
                            Text("\(option.number)")
                                .font(.caption.bold())
                                .frame(width: 24, height: 24)
                                .background(Color.accentColor.opacity(0.15))
                                .clipShape(Circle())
                        }
                        Text(option.label)
                            .font(.subheadline)
                        Spacer()
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .background(isSelected ? Color.accentColor : Color.secondary.opacity(0.12))
                    .foregroundStyle(isSelected ? .white : .primary)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .opacity(selectedId != nil && !isSelected ? 0.5 : 1.0)
                }
                .buttonStyle(.plain)
                .disabled(disabled || selectedId != nil)
                .accessibilityLabel("Option \(option.number): \(option.label)")
                .accessibilityIdentifier("chat_option_\(option.number)")
            }
        }
    }
}

// MARK: - Typing Indicator

struct TypingIndicatorBubble: View {
    @State private var animating = false

    var body: some View {
        HStack {
            HStack(spacing: 4) {
                ForEach(0..<3, id: \.self) { index in
                    Circle()
                        .fill(Color.secondary.opacity(0.5))
                        .frame(width: 8, height: 8)
                        .offset(y: animating ? -4 : 0)
                        .animation(
                            .easeInOut(duration: 0.4)
                                .repeatForever(autoreverses: true)
                                .delay(Double(index) * 0.15),
                            value: animating
                        )
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(Color.secondary.opacity(0.2))
            .clipShape(RoundedRectangle(cornerRadius: 16))
            Spacer(minLength: 60)
        }
        .onAppear { animating = true }
        .accessibilityLabel("Thinking...")
    }
}

// MARK: - Preview

#Preview {
    NavigationStack {
        IntakeChatScreen(projectState: .preview(step: .intake), client: MockWorkflowClient(delay: .zero))
    }
}

// MARK: - Summary Action

enum SummaryAction {
    case confirm
    case change
}

// MARK: - Summary Card

struct SummaryCard: View {
    let brief: DesignBrief
    let onAction: (SummaryAction) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Design Brief", systemImage: "doc.text")
                .font(.headline)

            BriefField(label: "Room Type", value: brief.roomType)

            if !brief.painPoints.isEmpty {
                BriefField(label: "Change", value: brief.painPoints.map { "• \($0)" }.joined(separator: "\n"))
            }

            if !brief.keepItems.isEmpty {
                BriefField(label: "Keep", value: brief.keepItems.map { "• \($0)" }.joined(separator: "\n"))
            }

            if let style = brief.styleProfile {
                if let mood = style.mood, !mood.isEmpty {
                    BriefField(label: "Mood", value: mood)
                }
                if !style.colors.isEmpty {
                    BriefField(label: "Colors", value: style.colors.joined(separator: ", "))
                }
            }

            Button("Looks Good!") {
                onAction(.confirm)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .frame(maxWidth: .infinity)
            .accessibilityIdentifier("chat_confirm_brief")

            Button("I want to change something") {
                onAction(.change)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .frame(maxWidth: .infinity)
            .accessibilityIdentifier("chat_change_brief")
        }
        .padding()
        .background(Color.secondary.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }
}

struct BriefField: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.subheadline)
        }
    }
}

// MARK: - Mode Button

struct ModeButton: View {
    let title: String
    let subtitle: String
    let icon: String
    let identifier: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 14) {
                Image(systemName: icon)
                    .font(.title2)
                    .foregroundStyle(Color.accentColor)
                    .frame(width: 40)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.headline)
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .foregroundStyle(.tertiary)
            }
            .padding()
            .background(Color.secondary.opacity(0.12))
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier(identifier)
    }
}
