# Gemini, Claude aur compatible models — Windows 0.1.1

API keys ke baghair app chal sakti hai. Cloud provider select karke save kar sakte hain; missing key par cloud unavailable rahega. Mock ko cloud response nahi kaha jata.

1. Overview mein Provider settings dabayein aur Desktop model providers panel kholen.
2. Google Gemini, Anthropic Claude ya Other compatible cloud API select karein.
3. Provider console se exact model ID enter karein. Gemini aur Claude ka official endpoint fixed hai. Compatible API ke liye HTTPS chat-completions endpoint enter karein.
4. API key abhi optional hai. Baad mein isi password field mein enter karein. Windows user ke protected vault mein key encrypted store hogi; desktop settings mein sirf reference rahega. Key chat ya project files mein paste na karein.
5. Save for next desktop launch dabayein. Tray se Exit JARVIS karke app dobara kholen; window close karna tray exit ka replacement nahi hai.
6. Cloud consent and budgets panel mein wohi exact endpoint aur model ID configure karein. Provider ki current input/output prices aur positive daily/per-task limits set karein; transmission consent aur cloud enable tab select karein jab aap cloud use karna chahte hon. Default cloud disabled aur allowance zero hai. UI mein budgets USD aur input/output rates USD per million tokens mein enter karein. Backend internally micro-USD use karta hai: 1 USD = 1,000,000 micro-USD. Reserved upper-bound usage actual provider invoice nahi hai.
7. Chat ya Coding Studio proposal submit karein. Project files cloud par sirf explicit scoped-context flow se bheje jate hain. Model proposals apply/test approval bypass nahi karte.

Provider selection chat aur coding proposals par apply hoti hai. Task planner ki configuration alag hai. Claude JSON proposals strict local validation use karte hain; native guaranteed structured-output mode ka claim nahi hai. Other compatible APIs ko chat/completions aur JSON response contract support karna hoga; har vendor ka proprietary API automatically compatible nahi hai.

Revoke protected key se key turant revoke hoti hai. Save aur full restart se persisted selection bhi clear/update karein. Portable credentials isi Windows user/machine se bound hain; doosre machine par dobara key add karein. Provider account/subscription aur API billing alag ho sakte hain.

Key ke baghair verification mein fake transport contracts, consent/budget blocking, revoked keys, native app settings aur unavailable state check ki jati hai. Real vendor response, account access aur billing keys ke baad verify honge. No automatic cloud fallback.

Official references: [Gemini compatible API](https://ai.google.dev/gemini-api/docs/openai), [Claude Messages API](https://platform.claude.com/docs/en/api/messages/create).
