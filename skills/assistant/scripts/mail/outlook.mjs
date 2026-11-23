/**
 * メールの層の Outlook の実装（クラシック Outlook の COM を操作する mailctl.ps1 を呼ぶ）。未実装。
 *
 * 操作の形は gmail.mjs の冒頭と同じ（listUnmarked・read・mark・archive・self・link）。印は Outlook の分類項目で持つ。
 * 作るには mailctl.ps1 に次が要る（Windows の端末で COM に当てて確かめながら作る）。
 *   - list の出力に、会話の ID（ConversationID）と分類項目（Categories）を足す
 *   - 分類項目を付ける動詞
 *   - archive を、使い手が承認した規則に当たった通にも使えるようにする
 * mailctl.ps1 のパスは設定の mailAutopilot.mail.mailctl で渡す。
 */
export async function openOutlook() {
  throw new Error('Outlook のメールの層はまだ無い（scripts/mail/outlook.mjs の冒頭）。試運転は Gmail の層で回す');
}
