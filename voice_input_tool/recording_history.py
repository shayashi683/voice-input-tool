"""Read or retry saved recordings without replaying a paste into an old target."""

from datetime import datetime

from voice_input_tool.recording_store import RecordingStore


def run_history_command(args, config, use_llm):
    store = RecordingStore()
    try:
        if args.history:
            for record in reversed(store.list()):
                date = datetime.fromtimestamp(record["created_at"]).isoformat(timespec="seconds")
                print(f'{record["id"]}  {date}  {record["state"]}  {len(record.get("text") or record.get("raw_text", ""))}文字')
            return 0
        if args.show_recording:
            record = store.read(args.show_recording)
            print(record.get("text") or record.get("raw_text", ""))
            return 0

        from voice_input_tool.recording_processor import RecordingProcessor
        from voice_input_tool.transcription import TranscriptionService

        source = store.read(args.retry_recording)
        # Retry into a new entry so neither an existing transcript nor a running job
        # can be overwritten by this command.
        samples = store.load_audio(source["id"])
        # An explicit retry uses the model selected for this invocation, while
        # automatic recovery retains the model stored on the source recording.
        new_id = store.create(
            use_llm=use_llm,
            asr_backend=config.get("asr_backend", "reazonspeech"),
            correction_provider="openai" if use_llm else None,
        )
        store.save_audio(new_id, (samples,))
        store.update(new_id, source_recording_id=source["id"], state="queued")
        worker = RecordingProcessor(store, TranscriptionService(config), None,
                                    on_status=lambda _: None, on_finished=lambda: None,
                                    notify=lambda *_: None)
        worker.process(new_id, deliver=False)
        record = store.read(new_id)
        print(record.get("text") or record.get("raw_text", ""))
        print(f"保存先: {store.directory(new_id)}")
        return 1 if record["state"] == "failed" else 0
    except (OSError, ValueError) as error:
        print(f"履歴を処理できませんでした: {error}")
        return 1
