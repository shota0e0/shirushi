//! One private local request, one terminal response, exit. No test-only commands.
fn main() {
    // Never expose payload, source location, locator or SDK panic strings.
    std::panic::set_hook(Box::new(|_| eprintln!("HELPER_PROCESS_FAILURE")));
    let result = std::panic::catch_unwind(|| {
        if std::env::args_os().len() != 1 {
            return Err(());
        }
        shirushi_f3a_rust_sdk_parity::helper_protocol::serve(
            &mut std::io::stdin().lock(),
            &mut std::io::stdout().lock(),
        )
        .map_err(|_| ())
    });
    if !matches!(result, Ok(Ok(()))) {
        eprintln!("HELPER_TRANSPORT_FAILURE");
        std::process::exit(2);
    }
}
