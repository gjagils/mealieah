import SwiftUI

struct ConnectView: View {
    @Environment(Session.self) private var session
    @State private var server = ""
    @State private var pin = ""
    @State private var busy = false
    @State private var errorText: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    TextField("https://recepten.voorbeeld.nl", text: $server)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                Section("Pincode van het gezin") {
                    SecureField("Pincode", text: $pin)
                        .keyboardType(.numberPad)
                }
                if let errorText {
                    Section { Text(errorText).foregroundStyle(.red) }
                }
                Section {
                    Button {
                        Task { await connect() }
                    } label: {
                        if busy { ProgressView() } else { Text("Verbinden") }
                    }
                    .disabled(server.isEmpty || busy)
                }
            }
            .navigationTitle("AH Recepten")
        }
        .onAppear { server = session.serverURL }
    }

    @MainActor
    private func connect() async {
        busy = true
        errorText = nil
        defer { busy = false }
        var text = server.trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.contains("://") { text = "https://" + text }
        guard let url = URL(string: text) else {
            errorText = "Ongeldig adres"
            return
        }
        do {
            let token = try await API.login(baseURL: url, pin: pin)
            session.serverURL = text
            session.token = token
            session.connected = true
        } catch {
            errorText = error.localizedDescription
        }
    }
}
