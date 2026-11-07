1. incident.io — agents that react to a live feed

The important design decision here is when the agent should speak. Constant commentary quickly becomes annoying. An agent that remembers what is happening, notices a meaningful change, and intervenes at the right moment is much more compelling.

Idea A: BenchBuddy — a lab partner that notices when you go wrong

Pitch: “An AI lab partner that watches your workbench, remembers your experiment, and catches mistakes as they happen.”

A student follows an electronics experiment, assembly guide, or practical worksheet. BenchBuddy watches the desk and listens to the student. It tracks the current step and reacts when the observed action conflicts with the instructions.

For example:
- You are supposed to measure resistance, but the visible meter setting is voltage.
- You move a component that was already checked.
- You complete a step, and it acknowledges progress without being asked.
- You repeat an unsuccessful action, and it offers a more useful explanation.
Its personality could be a slightly sarcastic but supportive lab demonstrator: “That is an excellent voltage measurement. Unfortunately, we are measuring resistance.”

Technical core
- A structured procedure containing steps, prerequisites, and observable completion conditions.
- Video observations combined across several frames, rather than treating every frame independently.
- A state machine tracking what has been completed and what remains uncertain.
- An intervention policy that considers confidence, importance, and whether it has already interrupted.
- Speech output and an overlay pointing at the relevant object.
The interesting engineering is connecting perception to procedural memory.

Achievable prototype: Use a large, clearly visible multimeter, three components, and a five-step task. Restrict detection to a few recognisable states. Small breadboard connections are a stretch goal.

Demo: Follow two steps correctly, deliberately select the wrong meter mode, and let BenchBuddy interrupt before you ask anything.

Why I like it: It has a real purpose, natural personality, and an obvious live reaction. Your electronics experience would also help you design a believable demonstration.

Main risk: Visual interpretation of tiny components. Design the physical setup around reliable observations.
Idea B: Stagehand — an AI that rescues your live presentation

Pitch: “A backstage assistant that follows your presentation and notices when the demo stops following the script.”

Stagehand observes a shared screen and microphone while the presenter gives a demo. It understands the planned sequence and reacts to mismatches:
- The presenter discusses a feature that is not visible.
- A loading screen persists.
- A live service fails.
- The presenter skips an essential step.
- A question from the audience needs an earlier diagram.
Instead of interrupting publicly, it displays a discreet cue: “The payment hasn’t completed yet. Open the transaction panel.”

Its personality is a calm theatre stage manager: short, precise, reassuring.

Technical core
- Speech-to-text aligned with presentation milestones.
- Screen-state recognition using screenshots, OCR, and known interface states.
- Detection of discrepancies between what is being said and what is visible.
- A recovery graph: for each failure, which useful fallback can the presenter choose?
- A private cue channel separate from the projected presentation.
Achievable prototype: One prepared web demo with three failure scenarios. Offer recovery buttons such as retry, show cached output, or open an explanatory diagram.

Demo: Deliberately break your own demo. Stagehand notices, identifies the problem, and guides the recovery.

Why it could stand out: The project demonstrates its value during its own presentation.

Main risk: Becoming a timer with AI commentary. The substantial feature must be matching live speech to actual screen state.
Idea C: Goblin Referee — a character that watches a physical game

Pitch: “Your board game now has an opinionated resident.”

A webcam watches a simple tabletop game. A goblin, sports commentator, or dungeon keeper reacts to moves, remembers rivalries, and calls out rule violations.

It might notice:
- A player collecting an item.
- An illegal move.
- A comeback after several losing turns.
- A player betraying an earlier agreement.
The personality develops from events: “You promised an alliance three turns ago. Fascinating interpretation of friendship.”

Technical core
- Computer vision translating a physical board into a structured game state.
- A rule engine distinguishing legal moves from mistakes.
- Event memory that supports callbacks and ongoing character relationships.
- A speech scheduler that avoids talking over players.
Achievable prototype: Build your own small grid game with coloured pieces or visual markers. Support movement, collecting, and stealing.

Demo: Ask a judge to move a piece. The goblin reacts immediately, then references that move later.

Why it could win: Audience participation, personality, and reliable event detection can make this exceptionally entertaining.

Main risk: Spending the weekend recognising arbitrary commercial board games. A deliberately designed game gives you much more control.

A practical implementation route exists: Gemini’s Live API supports real-time audio/video input and audio output. Your project’s contribution would be the event logic, memory, and reaction policy around that capability. ai.google.dev
2. Marshall Wace — “New Worlds”

For this challenge, I would make the discovery process itself interactive. A striking visual world helps presentation, but the judges should also be able to change something and see a meaningful computation happen.

You do not need to force a finance connection into this brief.

Idea A: ForkWorld — split a living city into alternative futures

Pitch: “What happens when two identical worlds make one different decision?”

A small simulated city contains residents, workplaces, roads, shops, and essential services. You pause it and create two branches.

In one world, a bridge closes. In another, a bus route is added. You then watch how consequences spread through the population.

The interesting results are distributional:
- Average travel time improves, but one neighbourhood becomes isolated.
- A new supermarket helps many residents while shifting congestion elsewhere.
- A road closure causes some workers to miss shifts, affecting businesses later.
Technical complexity
- Agents with individual destinations, schedules, and constraints.
- A transport graph and route selection.
- A discrete-event simulation.
- World snapshots and branching.
- Controlled randomness so that comparisons share the same external events.
- An explanation trail connecting an outcome to the events that produced it.
Creative element: The interface resembles a world explorer with a branching timeline. Judges move between futures rather than reading a static dashboard.

Achievable prototype: One fictional district, roughly 100–300 agents, and three interventions. Model transport and access first; add economic consequences only if time allows.

Demo
1. Run the baseline city.
2. Freeze it and create a branch.
3. Let a judge close a bridge.
4. Run both worlds simultaneously.
5. Select one resident and show why their day changed.
Presentation hook: “The average looks fine. Follow this person.”

Main risk: Claiming realistic prediction from a simplified model. Present it as an explorable simulation with explicit assumptions.

My assessment: Best overall Marshall Wace choice. It combines algorithms, systems engineering, visual storytelling, and audience interaction.
Idea B: Elsewhere — discover the city that different people can reach

Pitch: “Two people can live at the same address and inhabit completely different cities.”

Elsewhere reveals how someone’s reachable world changes with their circumstances:
- Step-free access.
- Limited walking distance.
- A small transport budget.
- Opening-hour constraints.
- A preference for quieter routes.
The map shows which opportunities remain reachable: lectures, shops, social spaces, or services.

The stronger feature is an intervention finder:
“If we could fix one barrier, which change would open access to the most destinations?”

Now the project goes beyond route planning into constrained optimisation.

Technical complexity
- A graph whose edges contain access requirements and travel costs.
- Reachability calculations under different constraints.
- Comparison of opportunity sets between profiles.
- Ranking candidate interventions by their marginal benefit.
- Explicit handling of missing accessibility information.
Creative element: A familiar place transforms as the user changes perspective. You reveal previously invisible boundaries.

Achievable prototype: One campus with a small, manually verified network. Include stairs, ramps, entrances, and one simulated lift outage.

Demo: Show two students travelling between the same buildings. Disable a lift. One student’s reachable world contracts. Add one intervention and reveal what becomes accessible again.

Presentation hook: “We didn’t build a new city. We revealed the different cities already inside it.”

Main risk: Missing information must remain “unknown,” rather than becoming an assumption of accessibility. Routing documentation itself recognises that data issues can prevent guarantees. WheelchairPreferencesInput

My assessment: Best combination of usefulness and emotional impact. The intervention optimiser is what gives it substantial technical depth.
Idea C: Terra Incognita — a map of unexplored connections in research

Pitch: “A research map that shows where an unexpected bridge might be worth investigating.”

Users explore clusters of papers and concepts. The system identifies potentially useful connections between fields that have related methods but relatively few observed links.

For example, it might connect a technique in ecology to a problem in computer networking, then explain:
- What the methods have in common.
- Which papers support the comparison.
- What would need to change for the transfer to work.
- What experiment could test the idea.
Technical complexity
- A citation and concept graph.
- Semantic representations of paper titles or available abstracts.
- Community detection.
- Candidate ranking combining semantic similarity, graph distance, and observed connectivity.
- Evidence retrieval for every proposed bridge.
OpenAlex provides programmatic access to scholarly works and related entities, making it a plausible source for a bounded research graph. OpenAlex Help Center

Achievable prototype: Two selected research fields and a few hundred papers. Curate a handful of genuinely interesting candidate connections.

Demo: Begin in one research cluster, reveal an unexpected neighbouring field, and open a proposed experiment supported by specific papers.

Presentation hook: “Search helps you find what you know to ask for. This helps you find the next question.”

Main risk: Sparse citations do not prove novelty. Describe these as candidate connections worth investigating, with coverage limitations visible.

My assessment: Best research-oriented option, particularly if your team enjoys data pipelines and graph algorithms.
Idea D: Genesis — evolve creatures for worlds that do not exist

Pitch: “Change the world, then watch life discover a way to survive.”

A simulated ecosystem contains creatures with inheritable properties: speed, perception, energy use, food preference, and simple behavioural rules.

Users alter the environment:
- Food becomes scarce.
- A landmass splits.
- A new food source appears.
- Moving becomes more expensive.
Across generations, different strategies emerge.

The distinctive feature is a lineage explorer: select a surviving creature and trace the mutations and environmental changes associated with its success.

Technical complexity
- Agent simulation.
- Mutation, inheritance, and selection.
- Spatial indexing for efficient local interactions.
- Reproducible runs.
- Population metrics and lineage tracking.
Achievable prototype: A polished 2D ecosystem with a few evolving traits. Evolve simple behaviour parameters before attempting neural controllers.

Demo: Split one ecosystem into two environments. Watch populations diverge, then compare their descendants.

Presentation hook: “We wrote the rules of the world. We did not explicitly write the survival strategy.”

Main risk: Outcomes can be slow or visually unclear. Tune the simulation for short generations and keep a reproducible replay available.

My assessment: Highest visual spectacle, with more risk of spending too long tuning the simulation.

For Marshall Wace specifically:
Idea	Main technical strength	Main presentation strength	Relative build risk
ForkWorld	Simulation, branching state, graph algorithms	Two futures diverging live	Medium–high
Elsewhere	Constrained routing and intervention optimisation	A familiar world changes with perspective	Medium
Terra Incognita	Graph analysis, semantic retrieval, evidence	An unexpected research connection	Medium
Genesis	Evolutionary computation and simulation	Visible emergence and adaptation	High


3. Capital One — accessible, independent account management

I read this brief as primarily about accessibility when using an account. The strongest project should demonstrate a specific barrier being removed and a real task being completed.

Idea A: ClearStep — one account task, presented in the way you need

Pitch: “The same banking task, with an interaction that fits the person.”

A user chooses preferences such as:
- One instruction at a time.
- Plain language.
- Read-aloud support.
- Large controls.
- Keyboard navigation.
They can then complete a task such as reviewing a statement, freezing a card, or preparing a payment in a mock account.

The key interaction is that switching presentation preserves progress. Someone can start with a keyboard, enable read-aloud halfway through, and continue from the same step.

Technical core
- A structured task model shared across all interaction modes.
- A state machine handling progress, validation, and recovery.
- Accessible interface components generated from that state.
- Optional language interpretation that proposes a structured action.
- A review step containing exact account values and the proposed action.
W3C’s cognitive accessibility guidance specifically supports clear, concise, step-by-step instructions as a way to reduce errors and support independence. W3C

Achievable prototype: Three tasks in a mock banking app. Make one task excellent across keyboard, screen reader, and simplified presentation.

Demo: Start a task, switch interaction mode halfway through, enter an incorrect value, correct it, and finish without losing progress.

Why it is strong: The accessibility features support actual task completion throughout the flow.

Main risk: Changing layout unpredictably. Let the user choose preferences and keep navigation stable.
Idea B: SwitchBank — manage an account with one reliable input

Pitch: “Check a balance and freeze a card using a single switch.”

Some users benefit from an interface controlled through one dependable input. SwitchBank cycles through available actions; the user selects the highlighted option.

Support:
- A keyboard key or external switch.
- Adjustable scanning speed.
- Pause and resume.
- Predictable grouping.
- Clear correction and confirmation controls.
Technical core
- A scanning-navigation engine.
- Focus management.
- Debouncing and accidental-input handling.
- Task-aware grouping so users do not scan through irrelevant controls.
- Persistent preferences.
Achievable prototype: Balance checking, transaction review, and card freezing, all usable with one key.

Demo: Put the mouse aside. Complete the entire flow using only the space bar.

Why it could stand out: Judges can directly experience the interaction constraint and see the task succeed.

Main risk: Overcomplicated gesture recognition. A reliable switch interface is already technically meaningful; webcam gestures can remain optional.

Measure scan cycles, selection errors, and completion time. Those make the engineering choices assessable.
Idea C: SideBySide — receive help without giving up account control

Pitch: “Someone can guide you through banking without becoming you.”

A user invites a trusted person into a limited support session. The supporter can explain a screen or point to a control, while the account holder retains control of consequential actions.

The account holder chooses whether to share:
- Interface structure only.
- Selected transaction details.
- A particular task.
- A time-limited session.
Technical core
- Two synchronised views of the same task.
- Server-enforced permissions.
- Field-level redaction.
- Guidance annotations.
- Expiring access and immediate revocation.
- An audit trail of assistance and actions.
Achievable prototype: Two browser windows showing a mock account. The supporter helps locate an unfamiliar transaction and prepare a query.

Demo: The supporter guides successfully, attempts an action outside their permissions, and is blocked. The account holder then revokes the session.

Why it is strong: It addresses the tension between needing assistance and retaining independence.

Main risk: Permissions implemented only in the interface. The backend must enforce them too.

For these prototypes, use synthetic accounts and a clear review-and-correct step for consequential actions. W3C’s financial error-prevention guidance supports mechanisms for checking, confirming, or reversing submissions. W3C
4. Brevan Howard — an AI researcher that tests itself

The strongest interpretation is an AI that can reject its own attractive story.

Give the language model responsibility for proposing and explaining hypotheses. Give a controlled statistical engine responsibility for computing results.

Idea A: Falsify — the AI researcher that tries to destroy its discovery

Pitch: “Most AI tools produce a convincing finding. Ours investigates whether it deserves to survive.”

The system proposes hypotheses from a dataset, records them, tests them, and actively looks for reasons they might be misleading.

Its research record contains:
- The exact hypothesis.
- The data used to develop it.
- Every attempted test.
- Effect size and uncertainty.
- Robustness checks.
- A final evaluation on untouched data.
Conclusions might be “supported in this evaluation,” “inconclusive,” or “not supported.”

Technical core
- Structured hypothesis generation.
- A locked discovery/validation/test split.
- An experiment registry recording failed attempts.
- Appropriate statistical tests and uncertainty estimates.
- Multiple-testing adjustments where applicable.
- Reproducible execution and an evidence-linked report.
Keeping evaluation data separate matters: scikit-learn documents how leakage and using test data for model choices can produce overly optimistic results. scikit-learn 1.9.1 documentation

Achievable prototype: One tabular domain, a restricted hypothesis language, and a fixed set of supported tests. Avoid unrestricted AI-generated analysis code.

Demo: Give it a synthetic dataset containing a planted effect and tempting noise features. Show which findings survive, including a plausible initial hypothesis it rejects.

Why it is my favourite: It matches the brief directly and makes the quality of the research process visible.

Main risk: Repeatedly inspecting the holdout until something succeeds. Freeze the candidate and evaluation plan before the final test.
Idea B: Mirage — watch impressive patterns emerge from nothing

Pitch: “How convincing a discovery can you manufacture from random data?”

Mirage lets the audience search increasingly many hypotheses in data with no planted relationship. Attractive-looking results begin to appear.

An AI researcher then audits them:
- How many alternatives were searched?
- Was the reported subgroup selected after seeing results?
- Does the result survive fresh data?
- Does the selection procedure itself generate similar findings under a null model?
Then introduce a dataset with a planted effect and compare what happens.

Technical core
- Synthetic data generators with known ground truth.
- A hypothesis-search engine.
- A full search history.
- Null simulations that repeat the search procedure.
- Multiple-testing controls.
- Independent replication.
SciPy provides false-discovery-rate adjustment methods, although the project still needs to choose a procedure suitable for its test assumptions. SciPy v1.18.0 Manual

Achievable prototype: A fixed menu of subgroup searches and transformations, with an AI agent proposing candidates and revising its conclusion after the audit.

Demo: Let a judge select the most convincing chart. Reveal that the dataset was random, then show whether it replicates.

Why it could win: The audience experiences the exact problem in the brief before seeing the solution.

Main risk: Becoming only an educational visualisation. Include the full propose–test–criticise–revise research loop.
Idea C: Signal Passport — test whether a discovery survives a new environment

Pitch: “A pattern earns credibility by surviving somewhere it was not discovered.”

A researcher finds a sports pattern in one season or league. The system freezes the claim and checks whether it transfers across time or settings.

It asks:
- Does it beat a simple baseline?
- Is the result dominated by one team?
- Does it persist in a later season?
- Does a plausible control variable weaken it?
- Is the effect large enough to matter?
Each hypothesis receives a “passport” documenting where it has and has not survived.

Technical core
- Time-aware evaluation.
- Baseline comparisons.
- Group-level robustness checks.
- Uncertainty estimates that respect repeated observations.
- Dataset and hypothesis versioning.
- AI-generated explanations grounded in computed results.
Achievable prototype: One sports dataset with several seasons and a small set of pre-match features.

Demo: Show a strong discovery-period result, then reveal that it fails in a later season. Compare it with a less dramatic but more stable candidate.

Why it is strong: It evaluates generalisation, which is more useful than presenting one impressive correlation.

Main risk: Leakage from information unavailable before the match. Make feature timing explicit.
What I would actually choose
If your priority is…	Choose	Build the demo around…
Marshall Wace: strongest overall package	ForkWorld	One intervention creates two visibly different futures
Marshall Wace: useful and emotionally memorable	Elsewhere	One barrier changes someone’s reachable world
incident.io: useful live AI	BenchBuddy	It catches a deliberate mistake without being prompted
Capital One: clearest brief fit	ClearStep	Someone completes a task while changing interaction modes
Brevan Howard: strongest research substance	Falsify	The AI rejects one of its own convincing hypotheses
Brevan Howard: strongest audience reveal	Mirage	The audience chooses a “discovery” from random data


My personal pick for Marshall Wace is ForkWorld. Put most of the effort into one excellent intervention, a genuinely working simulation, and an explanation of why one person’s outcome changed. That gives you technical complexity judges can inspect, creativity they can interact with, and a story they can remember.