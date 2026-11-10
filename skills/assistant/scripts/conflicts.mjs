/**
 * 予定の重なりを、問題ない（ok）・確かめる（check）・まずい（ng）に分ける。正本。
 *
 * 重なりは後から生まれ、直前まで気づかれないことがある。重なりは毎週大量にあり（勤務先の
 * 勤務場所の記載と会議、昼の「不在」と面談など）、全部を並べると読まれない。そこで
 * 「本人の判断が要る重なり」だけを拾う。
 *
 * 設定（パターン・自分のアドレス・例外）はインスタンスの conflict-rules.json。運用は docs/design.md「予定の重なり」。
 * 場所の判定（8）と勤務先への連絡（notices.mjs）は、conflict-rules.json の次の項目を使う。無ければ場所を見ない。
 *   sides.workplace … 決まった曜日に働く別の勤務先。pattern（場所欄・説明欄・題名の目印）、domains（主催者のドメイン）、
 *                     label（理由の文に入れる呼び名。既定「勤務先」）
 *   sides.home      … 本務の側。pattern・domains
 *   workplaceWindowPattern … 勤務場所の記載のうち、勤務先で働く時間帯を示すものの題名の目印
 *
 * 判定の考え方（上から順に当てる）
 *   1. 勤務場所の記載・終日の業務枠は予定ではないので、重なりの相手にしない
 *   2. 同じ時刻に始まり題名が似ているもの、どちらも会食の類のものは、同じ予定の重複登録とみなす（ok）
 *   3. 休みの日（年休・振替休日など）に入った予定は、単発なら ng、定例なら休む連絡が要るか check
 *   4. 「不在」（他人に予定を入れさせないための自衛の枠）は、自分が入れた・承諾した予定や会食なら ok、
 *      未回答の招待が入り込んでいれば check
 *   5. 学会・研修会など4時間以上の単発の枠の中は、同じ催しの一部（題名が似ている）なら ok、
 *      定例なら欠席の連絡が要るか check、別の単発の予定なら ng
 *   6. 定例どうしは check（系列の二重登録のことがある）。定例と単発は、単発を優先してよいので ok。
 *      ただし自分が主催する定例なら check
 *   7. 単発どうしは ng。片方が講義・講演など自分が話す予定なら必ず ng
 *   8. 場所：勤務先で働く時間帯（その日の勤務場所の記載のうち workplaceWindowPattern に当たるものの範囲）に、
 *      本務の側の対面の予定や自分が話す予定があれば ng。オンラインの予定は場所を問わない
 *   9. 自分が主催する時刻付きの予定が「予定なし」（transparency=transparent）なら、重なりが無くても check。
 *      他人がカレンダーの空きと見て予定を入れる。
 *      勤務場所の記載（workingLocation・「<拠点> (オフィス)」など）は性質上「予定なし」なので除く
 */
import { readFileSync, existsSync } from 'node:fs';
import { classifyEventTitle } from './event-kind.mjs';

/** インスタンスの conflict-rules.json を読む。無ければ null（重なりを見ない） */
export function loadRules(file) {
  return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : null;
}

/** 設定 RULES から判定の関数 { eventFacts, findConflicts } を作る */
export function conflictChecker(RULES) {
  const re = (s) => new RegExp(s, 'i');
  const DAYOFF = re(RULES.dayOffPattern);
  const SOCIAL = re(RULES.socialPattern);
  const SPEAKER = re(RULES.speakerPattern);
  const ONLINE = re(RULES.onlinePattern);
  // 場所の側：workplace（決まった曜日に働く別の勤務先）と home（本務）。どちらも任意で、無ければ other だけになる
  const SIDES = RULES.sides || {};
  const sideRe = (k) => (SIDES[k]?.pattern ? re(SIDES[k].pattern) : null);
  const WORK = sideRe('workplace');
  const HOME = sideRe('home');
  const WORK_DOMAINS = SIDES.workplace?.domains || [];
  const HOME_DOMAINS = SIDES.home?.domains || [];
  const WORK_LABEL = SIDES.workplace?.label || '勤務先';
  const WINDOW = RULES.workplaceWindowPattern ? re(RULES.workplaceWindowPattern) : null;
  const hit = (r, s) => Boolean(r && r.test(s));
  const ABSENT = re(RULES.absentPattern);
  const SKIPPABLE = re(RULES.skippablePattern);
  const TRIM = re(RULES.trimPattern);
  const SELF = new Set(RULES.selfAddresses.map((a) => a.toLowerCase()));

  /**
   * Calendar API の生のイベントから、重なりの判定に要る事実だけを取り出す。
   * 説明欄は場所と形式の判定にだけ使い、出力には残さない
   */
  function eventFacts(ev) {
    const title = (ev.summary || '(無題)').trim();
    const me = (ev.attendees || []).find((a) => a.self);
    const org = (ev.organizer?.email || '').toLowerCase();
    const organizerSelf = Boolean(ev.organizer?.self) || SELF.has(org);
    const response = organizerSelf ? 'organizer' : (me?.responseStatus || 'none');
    const online = Boolean(ev.hangoutLink || ev.conferenceData) || ONLINE.test(ev.location || '') || ONLINE.test(ev.description || '');
    // 場所の手がかりは、場所欄・説明欄 → 題名 → 主催者のドメインの順に強い。
    // 主催者が勤務先でも、会場が本務の側なら本務の側として扱う（勤務先の予定表から来る会合の会場が本務の側のことがある）
    const place = [ev.location || '', ev.description || ''].join(' ');
    let side = 'other';
    if (hit(HOME, place) && !hit(WORK, place)) side = 'home';
    else if (hit(WORK, place)) side = 'workplace';
    else if (hit(WORK, title)) side = 'workplace';
    else if (!organizerSelf && WORK_DOMAINS.some((d) => org.endsWith(d))) side = 'workplace';
    else if (!organizerSelf && HOME_DOMAINS.some((d) => org.endsWith(d))) side = 'home';
    const kind0 = classifyEventTitle(title);
    let kind = kind0;
    if (kind0 === 'appointment') {
      if (RULES.markerTitles.includes(title) || ev.eventType === 'workingLocation') kind = 'workplace';
      else if (DAYOFF.test(title)) kind = 'dayOff';
    }
    const allDay = Boolean(ev.start.date);
    const start = allDay ? `${ev.start.date}T00:00` : ev.start.dateTime.slice(0, 16);
    const end = allDay ? `${ev.end.date}T00:00` : ev.end.dateTime.slice(0, 16);
    return {
      title, kind, allDay, start, end,
      recurring: Boolean(ev.recurringEventId),
      response,
      online,
      side,
      organizer: org,
      speaker: SPEAKER.test(title),
      social: SOCIAL.test(title),
      attendees: (ev.attendees || []).length,
      organizerSelf,
      transparent: ev.transparency === 'transparent',
    };
  }

  const minutes = (iso) => Date.parse(`${iso}:00+09:00`) / 60000;
  const hours = (f) => (minutes(f.end) - minutes(f.start)) / 60;
  const norm = (s) => s.replace(/[\s　・／/（）()【】「」\[\]、。,.:：x×-]/g, '');
  /** 題名が n 文字以上の共通部分を持つか（数字だけの部分は数えない） */
  function similar(a, b, n) {
    const x = norm(a); const y = norm(b);
    for (let i = 0; i + n <= x.length; i++) {
      const g = x.slice(i, i + n);
      if (!/^\d+$/.test(g) && y.includes(g)) return true;
    }
    return false;
  }
  /** y が催しの枠 x の一部か。題名が似ているか、自分以外の同じ人が両方を入れている */
  const partOf = (y, x) => similar(x.title, y.title, 4) || Boolean(x.organizer && x.organizer === y.organizer && !SELF.has(x.organizer));
  const isContainer = (f) => f.kind === 'appointment' && !f.recurring && !f.allDay && hours(f) >= RULES.containerMinHours;
  const exceptionFor = (a, b) => RULES.exceptions.find((x) =>
    (a.title.includes(x.a) && b.title.includes(x.b)) || (a.title.includes(x.b) && b.title.includes(x.a)));

  /** 2つの予定の重なりを判定する。重なりの量の調整（切り上げられる予定・休んでよい会議）を後から当てる */
  function judgePair(a, b) {
    // 切り上げられる予定は、先頭の trimMinutes を過ぎた先で重なっているだけなら問題ない
    for (const [x, y] of [[a, b], [b, a]]) {
      if (TRIM.test(x.title) && minutes(y.start) >= minutes(x.start) + RULES.trimMinutes) {
        return { level: 'ok', reason: `${x.title}は${RULES.trimMinutes}分で切り上げられる` };
      }
    }
    const r = judgePair0(a, b);
    if (r.level === 'ng' && (SKIPPABLE.test(a.title) || SKIPPABLE.test(b.title))) {
      const s = SKIPPABLE.test(a.title) ? a : b;
      return { ...r, level: 'check', reason: `${r.reason}。${s.title}は休んでよい（できれば出る）` };
    }
    return r;
  }

  function judgePair0(a, b) {
    const ex = exceptionFor(a, b);
    if (ex) return { level: ex.level || 'ok', reason: ex.why };
    if (a.start === b.start && (similar(a.title, b.title, 3) || (a.social && b.social))) return { level: 'ok', reason: '同じ予定の重複登録' };
    if (a.social && b.social) return { level: 'ok', reason: '会食の重複登録' };
    for (const [x, y] of [[a, b], [b, a]]) {
      if (x.kind === 'dayOff') {
        if (y.kind === 'selfBlock' || y.social) return { level: 'ok', reason: '休みと自衛の枠・私的な予定' };
        if (y.response === 'accepted' || y.response === 'organizer') return { level: 'ok', reason: '休みの日に出ると決めた予定' };
        if (y.online && !y.recurring) return { level: 'check', reason: `休み（${x.title}）の日の TV 会議。出るか` };
        return y.recurring
          ? { level: 'check', reason: `休み（${x.title}）の日の定例。休む連絡が要るか` }
          : { level: 'ng', reason: `休み（${x.title}）の日に単発の予定が入っている` };
      }
    }
    for (const [x, y] of [[a, b], [b, a]]) {
      if (x.kind === 'selfBlock') {
        if (y.kind === 'selfBlock') return { level: 'ok', reason: '自衛の枠どうし' };
        if (y.social || y.response === 'organizer' || y.response === 'accepted') return { level: 'ok', reason: '自衛の枠に自分で入れた予定' };
        return { level: 'check', reason: '自衛の枠（不在）に未回答の招待が入っている' };
      }
    }
    if (isContainer(a) && isContainer(b) && similar(a.title, b.title, 4)) return { level: 'ok', reason: '同じ催しの枠の重複登録' };
    for (const [x, y] of [[a, b], [b, a]]) {
      if (isContainer(x) && minutes(y.start) >= minutes(x.start) && minutes(y.end) <= minutes(x.end) && !isContainer(y)) {
        if (y.partOf || partOf(y, x)) return { level: 'ok', reason: `「${x.title}」の一部` };
        if (y.recurring) return { level: 'check', reason: `「${x.title}」の間の定例。欠席の連絡が要るか`, within: x.title, other: y };
        return { level: 'ng', reason: `「${x.title}」の間に別の単発の予定`, within: x.title, other: y };
      }
    }
    if (a.speaker || b.speaker) {
      const [s, o] = a.speaker ? [a, b] : [b, a];
      if (o.recurring) return { level: 'check', reason: `自分が話す予定（${s.title}）と定例が重なる` };
      return { level: 'ng', reason: `自分が話す予定（${s.title}）と単発の予定が重なる` };
    }
    if (a.recurring && b.recurring) return { level: 'check', reason: '定例どうしが重なる（系列の二重登録のことがある）' };
    if (a.recurring !== b.recurring) {
      const r = a.recurring ? a : b;
      if (r.response === 'organizer' && r.attendees > 0) return { level: 'check', reason: `自分が主催する定例（${r.title}）と単発の予定が重なる` };
      return { level: 'ok', reason: '単発を優先してよい定例' };
    }
    return { level: 'ng', reason: '単発の予定どうしが重なる' };
  }

  /**
   * 予定の一覧（eventFacts の結果）から重なりを拾う。ok は件数だけ返す。
   * 返す各項目：{ date, level, reason, a: {start,end,title}, b?: {...} }
   */
  function findConflicts(facts) {
    const ev = facts.filter((f) => (!f.allDay || f.kind === 'dayOff') && !ABSENT.test(f.title));
    // 同じ予定が二重に入っていることがある（招待とその写しなど）。題名・開始・終了が同じものは1つにする
    const seen = new Set();
    for (let i = ev.length - 1; i >= 0; i--) {
      const k = `${ev[i].title}|${ev[i].start}|${ev[i].end}`;
      if (seen.has(k)) ev.splice(i, 1); else seen.add(k);
    }
    const out = [];
    let okCount = 0;
    const brief = (f) => ({ start: f.start.slice(11), end: f.end.slice(11), title: f.title });
    // 場所：勤務先で働く時間帯の、本務の側の対面の予定・自分が話す予定
    // 休みの日や、学会など4時間以上の枠の中にいる時間は、勤務場所の記載があっても勤務先にはいない
    const away = ev.filter((f) => f.kind === 'dayOff' || isContainer(f));
    const inside = (f, x) => minutes(f.start) >= minutes(x.start) && minutes(f.end) <= minutes(x.end);
    const workWindows = WINDOW ? facts.filter((f) => f.kind === 'workplace' && WINDOW.test(f.title) && !f.allDay) : [];
    for (const f of ev) {
      if (f.kind !== 'appointment' || f.online || isContainer(f)) continue;
      if (away.some((x) => x !== f && inside(f, x))) continue;
      const w = workWindows.find((w) => w.start.slice(0, 10) === f.start.slice(0, 10)
        && minutes(f.start) < minutes(w.end) && minutes(w.start) < minutes(f.end));
      if (!w) continue;
      if (f.side === 'home' || (f.speaker && f.side !== 'workplace')) {
        out.push({ date: f.start.slice(0, 10), level: 'ng', reason: `${WORK_LABEL}で働く時間（${w.start.slice(11)}-${w.end.slice(11)}）に${WORK_LABEL}の外での対面の予定`, a: brief(f) });
      }
    }
    // 自分の予定が「予定なし」：他人には空きに見える
    for (const f of ev) {
      if (f.transparent && f.organizerSelf && !f.allDay && f.kind !== 'workplace') {
        out.push({ date: f.start.slice(0, 10), level: 'check', reason: '自分の予定が「予定なし」になっていて、他人が空きと見て予定を入れられる', a: brief(f) });
      }
    }
    const seenWithin = new Set();
    // 同じ催しの枠が二重に入っているとき、どちらか一方の一部と分かれば一部として扱う
    const containers = ev.filter(isContainer);
    for (const f of ev) {
      f.partOf = !isContainer(f) && containers.some((c) => minutes(f.start) >= minutes(c.start) && minutes(f.end) <= minutes(c.end) && partOf(f, c));
    }
    const real = ev.filter((f) => f.kind !== 'workplace').sort((x, y) => minutes(x.start) - minutes(y.start));
    for (let i = 0; i < real.length; i++) {
      for (let j = i + 1; j < real.length; j++) {
        const a = real[i]; const b = real[j];
        if (minutes(b.start) >= minutes(a.end)) continue;
        const { level, reason, within, other } = judgePair(a, b);
        if (level === 'ok') { okCount++; continue; }
        const date = (a.allDay ? b : a).start.slice(0, 10);
        // 同じ催しの枠が二重に入っているとき、枠の中の同じ予定を二度挙げない
        if (within) {
          const k = `${date}|${level}|${other.title}|${other.start}`;
          if (seenWithin.has(k)) continue;
          seenWithin.add(k);
        }
        out.push({ date, level, reason, a: brief(a), b: brief(b) });
      }
    }
    const rank = { ng: 0, check: 1 };
    out.sort((x, y) => x.date.localeCompare(y.date) || rank[x.level] - rank[y.level]);
    return { okCount, items: out };
  }

  return { eventFacts, findConflicts };
}
