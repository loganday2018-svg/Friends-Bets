"""
NFL Betting Tracker - A complete Streamlit app for tracking weekly NFL bets among friends.

This app manages a betting league with SQLite persistence, enforces league rules,
and provides standings and cumulative points visualization.
"""

import streamlit as st
import pandas as pd
import sqlite3
import altair as alt
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import io
from hashlib import sha256


# ==================== BUSINESS LOGIC FUNCTIONS ====================

def american_profit(odds: int, stake: float = 100.0) -> float:
    """
    Return profit (not payout) if a bet with American odds wins.
    
    Args:
        odds: American odds (e.g., +320 or -150)
        stake: Amount wagered
        
    Returns:
        Profit amount if bet wins
    """
    if odds > 0:
        return stake * (odds / 100)
    else:
        return stake * (100 / abs(odds))


def bet_points(odds: int, result: str, is_triple: bool, stake: float = 100.0) -> float:
    """
    Return points for a single bet following the league rules.
    
    Args:
        odds: American odds
        result: 'win', 'loss', 'push', or 'pending'
        is_triple: Whether this bet is the weekly triple
        stake: Amount wagered
        
    Returns:
        Points earned/lost for this bet
    """
    if result == 'pending':
        return 0.0
    elif result == 'push':
        return 0.0
    elif result == 'win':
        profit = american_profit(odds, stake)
        return profit * 3 if is_triple else profit
    elif result == 'loss':
        # Triple losses are capped at -100
        return -100.0 if is_triple else -stake
    else:
        return 0.0


def weekly_points(df_bets_for_week: pd.DataFrame) -> pd.DataFrame:
    """Calculate weekly point totals for each player."""
    if df_bets_for_week.empty:
        return pd.DataFrame(columns=['player_id', 'player_name', 'week', 'points'])
    
    # Calculate points for each bet
    df_bets_for_week['points'] = df_bets_for_week.apply(
        lambda row: bet_points(row['american_odds'], row['result'], 
                              bool(row['is_triple']), row['stake']), axis=1
    )
    
    # Group by player and sum points
    weekly_totals = df_bets_for_week.groupby(['player_id', 'player_name', 'week'])['points'].sum().reset_index()
    return weekly_totals


def season_standings(df_all_bets: pd.DataFrame) -> pd.DataFrame:
    """Calculate season-long standings for all players."""
    if df_all_bets.empty:
        return pd.DataFrame(columns=['player_name', 'total_bets', 'wins', 'losses', 'pushes', 'season_points'])
    
    # Calculate points for each bet
    df_all_bets['points'] = df_all_bets.apply(
        lambda row: bet_points(row['american_odds'], row['result'], 
                              bool(row['is_triple']), row['stake']), axis=1
    )
    
    # Group by player and calculate stats
    standings = df_all_bets.groupby(['player_id', 'player_name']).agg({
        'id': 'count',  # total bets
        'points': 'sum',  # season points
        'result': lambda x: (x == 'win').sum(),  # wins
    }).rename(columns={'id': 'total_bets', 'result': 'wins'}).reset_index()
    
    # Calculate losses and pushes
    loss_counts = df_all_bets[df_all_bets['result'] == 'loss'].groupby('player_id').size()
    push_counts = df_all_bets[df_all_bets['result'] == 'push'].groupby('player_id').size()
    
    standings['losses'] = standings['player_id'].map(loss_counts).fillna(0).astype(int)
    standings['pushes'] = standings['player_id'].map(push_counts).fillna(0).astype(int)
    standings['season_points'] = standings['points'].round(1)
    
    # Sort by points descending
    standings = standings.sort_values('season_points', ascending=False).reset_index(drop=True)
    
    return standings[['player_name', 'total_bets', 'wins', 'losses', 'pushes', 'season_points']]


def cumulative_by_week(df_all_bets: pd.DataFrame) -> pd.DataFrame:
    """Calculate cumulative points by week for charting."""
    if df_all_bets.empty:
        return pd.DataFrame(columns=['player_name', 'week', 'cumulative_points', 'color'])
    
    # Calculate points for each bet
    df_all_bets['points'] = df_all_bets.apply(
        lambda row: bet_points(row['american_odds'], row['result'], 
                              bool(row['is_triple']), row['stake']), axis=1
    )
    
    # Group by player and week, sum points
    weekly_totals = df_all_bets.groupby(['player_id', 'player_name', 'color', 'week'])['points'].sum().reset_index()
    
    # Calculate cumulative points
    weekly_totals = weekly_totals.sort_values(['player_id', 'week'])
    weekly_totals['cumulative_points'] = weekly_totals.groupby('player_id')['points'].cumsum()
    
    return weekly_totals[['player_name', 'week', 'cumulative_points', 'color']]


# ==================== UNIT TESTS (Run at Import) ====================

def run_tests():
    """Run basic unit tests for business logic functions."""
    tolerance = 1e-6
    
    # Test american_profit
    assert abs(american_profit(320, 100) - 320) < tolerance, "american_profit(+320) failed"
    assert abs(american_profit(-150, 100) - 66.66666666666667) < tolerance, "american_profit(-150) failed"
    
    # Test bet_points
    assert abs(bet_points(320, 'win', True, 100) - 960) < tolerance, "bet_points triple win failed"
    assert abs(bet_points(320, 'loss', True, 100) - (-100)) < tolerance, "bet_points triple loss failed"
    assert abs(bet_points(-120, 'loss', False, 100) - (-100)) < tolerance, "bet_points regular loss failed"
    assert abs(bet_points(-120, 'win', False, 100) - 83.33333333333333) < tolerance, "bet_points regular win failed"
    
    print("✅ All unit tests passed!")

# Run tests on import
run_tests()


# ==================== DATABASE FUNCTIONS ====================

def init_database():
    """Initialize SQLite database with tables and seed data."""
    conn = sqlite3.connect('nfl_bets.db')
    cursor = conn.cursor()
    
    # Create players table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            color TEXT DEFAULT '#1f77b4'
        )
    """)
    
    # Create bets table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            description TEXT,
            american_odds INTEGER NOT NULL,
            stake REAL NOT NULL DEFAULT 100,
            is_triple INTEGER NOT NULL DEFAULT 0,
            result TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(player_id) REFERENCES players(id)
        )
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_player_week ON bets(player_id, week)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_week ON bets(week)")
    
    # Check if we need seed data
    cursor.execute("SELECT COUNT(*) FROM players")
    if cursor.fetchone()[0] == 0:
        # Insert seed players
        players = [
            ('Logan', '#e74c3c'),
            ('Caroline', '#3498db'),
            ('James', '#2ecc71'),
            ('Scippy', '#f39c12')
        ]
        cursor.executemany("INSERT INTO players (name, color) VALUES (?, ?)", players)
        
        # Insert seed bets
        seed_bets = [
            # Week 1 - Logan
            (1, 1, "Chiefs -7.5", -110, 100, 0, 'win'),
            (1, 1, "Over 47.5", +320, 100, 1, 'win'),  # Triple win = 960 points
            (1, 1, "Bills ML", -150, 100, 0, 'loss'),
            (1, 1, "Cowboys +3", +120, 100, 0, 'win'),
            (1, 1, "Packers Under 24.5", -105, 100, 0, 'push'),
            
            # Week 1 - Caroline
            (1, 2, "Ravens -3", -120, 100, 0, 'win'),
            (1, 2, "Eagles ML", +180, 100, 0, 'loss'),
            (1, 2, "Saints +6.5", -110, 100, 1, 'loss'),  # Triple loss = -100
            (1, 2, "49ers Over 21", +105, 100, 0, 'win'),
            (1, 2, "Dolphins -4", -115, 100, 0, 'push'),
            
            # Week 1 - James
            (1, 3, "Bengals +2.5", +110, 100, 0, 'win'),
            (1, 3, "Rams Under 45", -105, 100, 0, 'win'),
            (1, 3, "Cardinals ML", +250, 100, 1, 'loss'),  # Triple loss = -100
            (1, 3, "Jets +7", -110, 100, 0, 'loss'),
            (1, 3, "Titans Over 18.5", +125, 100, 0, 'win'),
            
            # Week 1 - Scippy
            (1, 4, "Broncos -1", -105, 100, 0, 'loss'),
            (1, 4, "Steelers +4.5", +115, 100, 0, 'win'),
            (1, 4, "Colts ML", -140, 100, 1, 'win'),  # Triple win
            (1, 4, "Seahawks Over 23", -110, 100, 0, 'win'),
            (1, 4, "Panthers +14", +120, 100, 0, 'loss'),
            
            # Week 2 - Logan
            (2, 1, "Chiefs -3.5", -115, 100, 0, 'win'),
            (2, 1, "Under 52.5", -105, 100, 0, 'loss'),
            (2, 1, "Bills +2.5", +110, 100, 1, 'win'),  # Triple win
            (2, 1, "Cowboys ML", +155, 100, 0, 'loss'),
            (2, 1, "Packers -6", -120, 100, 0, 'win'),
            
            # Week 2 - Caroline
            (2, 2, "Ravens +1", +105, 100, 1, 'loss'),  # Triple loss = -100
            (2, 2, "Eagles -4", -110, 100, 0, 'win'),
            (2, 2, "Saints ML", +175, 100, 0, 'win'),
            (2, 2, "49ers Under 26.5", -115, 100, 0, 'push'),
            (2, 2, "Dolphins +7.5", +120, 100, 0, 'win'),
            
            # Week 2 - James
            (2, 3, "Bengals ML", -125, 100, 0, 'loss'),
            (2, 3, "Rams +3", +115, 100, 0, 'win'),
            (2, 3, "Cardinals +8.5", -110, 100, 0, 'win'),
            (2, 3, "Jets ML", +280, 100, 1, 'win'),  # Triple win = 840 points
            (2, 3, "Titans -2.5", -105, 100, 0, 'loss'),
            
            # Week 2 - Scippy
            (2, 4, "Broncos +4", +120, 100, 0, 'win'),
            (2, 4, "Steelers -1.5", -110, 100, 0, 'loss'),
            (2, 4, "Colts +6", +115, 100, 0, 'win'),
            (2, 4, "Seahawks ML", +190, 100, 1, 'loss'),  # Triple loss = -100
            (2, 4, "Panthers Over 17.5", +105, 100, 0, 'win'),
        ]
        
        cursor.executemany("""
            INSERT INTO bets (week, player_id, description, american_odds, stake, is_triple, result)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, seed_bets)
    
    conn.commit()
    conn.close()


@st.cache_data
def get_players() -> pd.DataFrame:
    """Fetch all players from database."""
    conn = sqlite3.connect('nfl_bets.db')
    df = pd.read_sql_query("SELECT * FROM players ORDER BY name", conn)
    conn.close()
    return df


@st.cache_data
def get_bets(week: Optional[int] = None, player_id: Optional[int] = None, 
             bet_type: Optional[str] = None, start_date: Optional[datetime] = None, 
             end_date: Optional[datetime] = None) -> pd.DataFrame:
    """Fetch bets with optional filtering."""
    conn = sqlite3.connect('nfl_bets.db')
    
    query = """
        SELECT b.*, p.name as player_name, p.color
        FROM bets b
        JOIN players p ON b.player_id = p.id
    """
    params = []
    conditions = []
    
    if week is not None:
        conditions.append("b.week = ?")
        params.append(week)
    
    if player_id is not None:
        conditions.append("b.player_id = ?")
        params.append(player_id)
    
    if bet_type is not None:
        if bet_type == "single":
            conditions.append("b.is_triple = 0")
        elif bet_type == "triple":
            conditions.append("b.is_triple = 1")
        # Add more bet types as needed
    
    if start_date is not None:
        conditions.append("b.created_at >= ?")
        params.append(start_date.strftime('%Y-%m-%d'))
    
    if end_date is not None:
        conditions.append("b.created_at <= ?")
        params.append(end_date.strftime('%Y-%m-%d'))
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    query += " ORDER BY b.week, p.name, b.id"
    
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def insert_bet(week: int, player_id: int, description: str, american_odds: int, 
               stake: float, is_triple: bool, result: str = 'pending'):
    """Insert a new bet into the database."""
    conn = sqlite3.connect('nfl_bets.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO bets (week, player_id, description, american_odds, stake, is_triple, result)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (week, player_id, description, american_odds, stake, int(is_triple), result))
    
    conn.commit()
    conn.close()


def update_bet(bet_id: int, description: str, odds: int, stake: float, is_triple: bool, result: str):
    """Update an existing bet."""
    conn = sqlite3.connect('nfl_bets.db')
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE bets
        SET description = ?, american_odds = ?, stake = ?, is_triple = ?, result = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (description, odds, stake, int(is_triple), result, bet_id)
    )

    conn.commit()
    conn.close()


def update_bet_result(bet_id: int, result: str):
    """Update the result of a bet."""
    conn = sqlite3.connect('nfl_bets.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE bets SET result = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (result, bet_id))
    
    conn.commit()
    conn.close()


def add_player(name: str, color: str = '#1f77b4'):
    """Add a new player to the database."""
    conn = sqlite3.connect('nfl_bets.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute("INSERT INTO players (name, color) VALUES (?, ?)", (name, color))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def delete_player(player_id: int):
    """Delete a player and all their bets."""
    conn = sqlite3.connect('nfl_bets.db')
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM bets WHERE player_id = ?", (player_id,))
    cursor.execute("DELETE FROM players WHERE id = ?", (player_id,))
    
    conn.commit()
    conn.close()


def reset_week(week: int):
    """Delete all bets for a specific week."""
    conn = sqlite3.connect('nfl_bets.db')
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM bets WHERE week = ?", (week,))
    
    conn.commit()
    conn.close()


def reset_database():
    """Delete all data from the database."""
    conn = sqlite3.connect('nfl_bets.db')
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM bets")
    cursor.execute("DELETE FROM players")
    
    conn.commit()
    conn.close()


def update_player(player_id: int, name: str, color: str):
    """Update an existing player."""
    conn = sqlite3.connect('nfl_bets.db')
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE players
        SET name = ?, color = ?
        WHERE id = ?
        """,
        (name, color, player_id)
    )

    conn.commit()
    conn.close()
    
    # Clear cache to refresh data
    get_players.clear()
    get_bets.clear()


# ==================== STREAMLIT UI ====================

def main():
    """Main Streamlit application."""
    st.set_page_config(
        page_title="NFL Betting Tracker",
        page_icon="🏈",
        layout="wide"
    )
    
    # Initialize database
    init_database()
    
    st.title("🏈 NFL Betting Tracker")
    st.markdown("Track weekly NFL bets among friends with league rules and standings.")
    
    # Login/logout functionality
    from hashlib import sha256

# Mock user database
USER_DB = {
    "friend1": sha256("password1".encode()).hexdigest(),
    "friend2": sha256("password2".encode()).hexdigest(),
    "Logan": sha256("Day".encode()).hexdigest(),
    "Lincoln": sha256("Parsley".encode()).hexdigest(),
    "Zach": sha256("Meyer".encode()).hexdigest(),
}    # Login function
    def login():
        """Handle user login."""
        st.sidebar.header("Login")
        username = st.sidebar.text_input("Username")
        password = st.sidebar.text_input("Password", type="password")

        if st.sidebar.button("Login"):
            hashed_password = sha256(password.encode()).hexdigest()
            if username in USER_DB and USER_DB[username] == hashed_password:
                st.session_state["logged_in"] = True
                st.session_state["username"] = username
                st.sidebar.success(f"Welcome, {username}!")
                st.rerun()  # Trigger a rerun to update the interface
            else:
                st.sidebar.error("Invalid username or password.")

    # Logout function
    def logout():
        """Handle user logout."""
        if st.sidebar.button("Logout"):
            st.session_state["logged_in"] = False
            st.sidebar.info("Logged out.")

    # Main app logic
    if "logged_in" not in st.session_state:
        st.session_state["logged_in"] = False

    if st.session_state["logged_in"]:
        st.sidebar.header(f"Logged in as {st.session_state['username']}")
        logout()
        
        # Create tabs
        tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs(["Enter Bets", "Grade Results", "Standings", "Charts", "Admin", "Bet History", "Data Visualization", "Historical Analysis"])
    
        with tab1:
            enter_bets_tab()
    
        with tab2:
            grade_results_tab()
    
        with tab3:
            standings_tab()
    
        with tab4:
            charts_tab()
    
        with tab5:
            admin_tab()
    
        with tab6:
            bet_history_tab()
    
        with tab7:
            visualize_data_tab()
    
        with tab8:
            historical_analysis_tab()

    else:
        login()


def enter_bets_tab():
    """Tab for entering new bets."""
    st.header("Enter Bets")
    
    players = get_players()
    if players.empty:
        st.warning("No players found. Please add players in the Admin tab.")
        return
    
    col1, col2 = st.columns(2)
    
    with col1:
        week = st.number_input("Week", min_value=1, max_value=18, value=1)
    
    with col2:
        player_name = st.selectbox("Player", players['name'].tolist())
        player_id = players[players['name'] == player_name]['id'].iloc[0]
    
    # Get existing bets for this player/week
    existing_bets = get_bets(week=week, player_id=player_id)
    
    # Check triple usage
    has_triple = (existing_bets['is_triple'] == 1).any() if not existing_bets.empty else False
    bet_count = len(existing_bets)
    
    # Status badges
    col1, col2 = st.columns(2)
    with col1:
        if bet_count == 5:
            st.success(f"✅ Bets entered: {bet_count}/5")
        elif bet_count < 5:
            st.warning(f"⚠️ Bets entered: {bet_count}/5")
        else:
            st.error(f"❌ Too many bets: {bet_count}/5")
    
    with col2:
        if has_triple:
            st.success("✅ Triple used")
        else:
            st.info("❌ Triple not used")
    
    st.subheader("Add New Bet")
    
    with st.form("bet_form"):
        col1, col2 = st.columns(2)
        
        with col1:
            description = st.text_input("Description", placeholder="Chiefs -7.5")
            american_odds = st.number_input("American Odds", value=0, 
                                          help="Enter odds like +320 or -150")
        
        with col2:
            stake = st.number_input("Stake", min_value=1.0, value=100.0, step=1.0)
            is_triple = st.checkbox("Triple Bet", disabled=has_triple,
                                  help="Only one triple bet allowed per week")
        
        result = st.selectbox("Result", ["pending", "win", "loss", "push"], index=0)
        
        submitted = st.form_submit_button("Add Bet")
        
        if submitted:
            # Validation
            errors = []
            
            if not description.strip():
                errors.append("Description is required")
            
            if american_odds == 0:
                errors.append("American odds cannot be zero")
            
            if is_triple and has_triple:
                errors.append("Player already has a triple bet for this week")
            
            if bet_count >= 5:
                errors.append("Player already has 5 bets for this week")
            
            if errors:
                for error in errors:
                    st.error(error)
            else:
                try:
                    insert_bet(week, player_id, description.strip(), american_odds, 
                             stake, is_triple, result)
                    st.success("Bet added successfully!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error adding bet: {e}")
    
    # Show existing bets
    if not existing_bets.empty:
        st.subheader(f"{player_name}'s Bets - Week {week}")
        
        # Calculate points for display
        existing_bets['points'] = existing_bets.apply(
            lambda row: bet_points(row['american_odds'], row['result'], 
                                 bool(row['is_triple']), row['stake']), axis=1
        )
        
        # Format for display
        display_df = existing_bets[['description', 'american_odds', 'stake', 'is_triple', 'result', 'points']].copy()
        display_df['is_triple'] = display_df['is_triple'].map({1: '✅', 0: ''})
        display_df['points'] = display_df['points'].round(1)
        display_df.columns = ['Description', 'Odds', 'Stake', 'Triple', 'Result', 'Points']
        
        st.dataframe(display_df, use_container_width=True)
        
        total_points = existing_bets['points'].sum()
        st.metric("Week Total", f"{total_points:.1f} points")


def grade_results_tab():
    """Tab for grading and editing bet results with mobile responsiveness."""
    st.header("Grade Results")

    players = get_players()
    if players.empty:
        st.warning("No players found.")
        return

    # Use responsive layout with Streamlit columns and expanders
    with st.expander("Filters", expanded=True):
        col1, col2, col3 = st.columns([1, 1, 1])

        with col1:
            week_filter = st.selectbox("Week", ["All"] + list(range(1, 19)), index=0)

        with col2:
            player_filter = st.selectbox("Player", ["All"] + players['name'].tolist(), index=0)

        with col3:
            bet_type_filter = st.selectbox("Bet Type", ["All", "Single", "Parlay", "Teaser"], index=0)

        # Add date range filter
        start_date = st.date_input("Start Date", value=None, key="start_date")
        end_date = st.date_input("End Date", value=None, key="end_date")

    # Get filtered bets
    week = None if week_filter == "All" else week_filter
    player_id = None if player_filter == "All" else players[players['name'] == player_filter]['id'].iloc[0]
    bet_type = None if bet_type_filter == "All" else bet_type_filter.lower()

    bets_df = get_bets(week=week, player_id=player_id, bet_type=bet_type, start_date=start_date, end_date=end_date)

    if bets_df.empty:
        st.info("No bets found for the selected filters.")
        return

    st.subheader("Edit Bets")

    # Group bets for editing
    for _, bet in bets_df.iterrows():
        with st.container():
            col1, col2, col3, col4 = st.columns([3, 2, 2, 1])

            with col1:
                description = st.text_input(
                    "Description", bet['description'], key=f"desc_{bet['id']}"
                )
                odds = st.number_input(
                    "Odds", value=bet['american_odds'], key=f"odds_{bet['id']}"
                )

            with col2:
                stake = st.number_input(
                    "Stake", value=bet['stake'], key=f"stake_{bet['id']}"
                )
                is_triple = st.checkbox(
                    "Triple Bet", value=bool(bet['is_triple']), key=f"triple_{bet['id']}"
                )

            with col3:
                result = st.selectbox(
                    "Result", ["pending", "win", "loss", "push"], index=["pending", "win", "loss", "push"].index(bet['result']), key=f"result_{bet['id']}"
                )

            with col4:
                if st.button("Update", key=f"update_{bet['id']}"):
                    try:
                        update_bet(
                            bet['id'], description, odds, stake, is_triple, result
                        )
                        st.success("Bet updated successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error updating bet: {e}")

    # Add a button to delete all bets with confirmation checkbox
    confirm_delete = st.checkbox("I confirm that I want to delete all old results.", key="confirm_delete")
    if st.button("Delete All Bets", key="delete_bets"):
        if confirm_delete:
            try:
                delete_all_bets()
                st.success("All bets deleted successfully!")
                st.rerun()
            except Exception as e:
                st.error(f"Error deleting bets: {e}")
        else:
            st.warning("Please confirm the action by checking the box.")


def standings_tab():
    """Tab for displaying standings."""
    st.header("Standings")
    
    # Toggle for season vs weekly view
    view_type = st.radio("View", ["Season", "Weekly"], horizontal=True)
    
    all_bets = get_bets()
    
    if all_bets.empty:
        st.info("No bets found.")
        return
    
    if view_type == "Season":
        st.subheader("Season Standings")
        standings = season_standings(all_bets)
        
        if not standings.empty:
            # Add ranking
            standings.insert(0, 'Rank', range(1, len(standings) + 1))
            st.dataframe(standings, use_container_width=True, hide_index=True)
            
            # Download button
            csv = standings.to_csv(index=False)
            st.download_button(
                label="📥 Download Season Standings",
                data=csv,
                file_name=f"season_standings_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )
    
    else:  # Weekly view
        week = st.selectbox("Select Week", sorted(all_bets['week'].unique()), index=0)
        
        st.subheader(f"Week {week} Standings")
        week_bets = all_bets[all_bets['week'] == week]
        
        if not week_bets.empty:
            weekly_totals = weekly_points(week_bets)
            weekly_totals = weekly_totals.sort_values('points', ascending=False).reset_index(drop=True)
            weekly_totals.insert(0, 'Rank', range(1, len(weekly_totals) + 1))
            
            display_weekly = weekly_totals[['Rank', 'player_name', 'points']].copy()
            display_weekly.columns = ['Rank', 'Player', 'Points']
            display_weekly['Points'] = display_weekly['Points'].round(1)
            
            st.dataframe(display_weekly, use_container_width=True, hide_index=True)


def charts_tab():
    """Tab for displaying charts."""
    st.header("Charts")
    
    all_bets = get_bets()
    
    if all_bets.empty:
        st.info("No bets found.")
        return
    
    # Get cumulative data
    cumulative_data = cumulative_by_week(all_bets)
    
    if cumulative_data.empty:
        st.info("No completed bets found for charting.")
        return
    
    st.subheader("Cumulative Points by Week")
    
    # Player highlight option
    players = cumulative_data['player_name'].unique()
    highlight_player = st.selectbox("Highlight Player (optional)", ["None"] + list(players))
    
    # Create chart
    if highlight_player != "None":
        # Highlight specific player
        base_chart = alt.Chart(cumulative_data[cumulative_data['player_name'] != highlight_player]).mark_line(
            opacity=0.3, strokeWidth=2
        ).encode(
            x=alt.X('week:O', title='Week'),
            y=alt.Y('cumulative_points:Q', title='Cumulative Points'),
            color=alt.Color('player_name:N', legend=alt.Legend(title="Player")),
            tooltip=['player_name:N', 'week:O', 'cumulative_points:Q']
        )
        
        highlight_chart = alt.Chart(cumulative_data[cumulative_data['player_name'] == highlight_player]).mark_line(
            strokeWidth=4
        ).encode(
            x=alt.X('week:O'),
            y=alt.Y('cumulative_points:Q'),
            color=alt.Color('player_name:N'),
            tooltip=['player_name:N', 'week:O', 'cumulative_points:Q']
        )
        
        chart = (base_chart + highlight_chart).resolve_scale(color='independent')
    else:
        # Show all players equally
        chart = alt.Chart(cumulative_data).mark_line(strokeWidth=3).encode(
            x=alt.X('week:O', title='Week'),
            y=alt.Y('cumulative_points:Q', title='Cumulative Points'),
            color=alt.Color('player_name:N', legend=alt.Legend(title="Player")),
            tooltip=['player_name:N', 'week:O', 'cumulative_points:Q']
        )
    
    chart = chart.properties(width=700, height=400)
    st.altair_chart(chart, use_container_width=True)
    
    # Show current standings in sidebar
    with st.expander("Current Standings"):
        standings = season_standings(all_bets)
        st.dataframe(standings[['player_name', 'total_bets', 'wins', 'losses', 'pushes', 'season_points']], use_container_width=True)


# Placeholder for admin tab functionality
def admin_tab():
    """Tab for managing players with pagination."""
    st.header("Admin")

    players = get_players()

    if players.empty:
        st.warning("No players found.")
        return

    search_query = st.text_input("Search Player", placeholder="Enter player name", key="admin_search")
    filtered_players = players[players['name'].str.contains(search_query, case=False)] if search_query else players

    st.subheader("Edit Bettors")

    paginated_players = paginate_dataframe(filtered_players, page_size=5, key="admin_pagination")

    for _, player in paginated_players.iterrows():
        with st.container():
            col1, col2, col3 = st.columns([3, 2, 1])

            with col1:
                name = st.text_input(
                    "Name", player['name'], key=f"name_{player['id']}"
                )

            with col2:
                color = st.color_picker(
                    "Color", player['color'], key=f"color_{player['id']}"
                )

            with col3:
                if st.button("Update", key=f"update_player_{player['id']}"):
                    try:
                        update_player(player['id'], name, color)
                        st.success("Player updated successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error updating player: {e}")


# Update mock user database
USER_DB = {
    "friend1": sha256("password1".encode()).hexdigest(),
    "friend2": sha256("password2".encode()).hexdigest(),
    "Logan": sha256("Day".encode()).hexdigest(),
}

# Add password management functionality
def manage_passwords_tab():
    """Tab for managing usernames and passwords."""
    st.header("Manage User Passwords")

    # Display current users
    st.subheader("Current Users")
    for user in USER_DB.keys():
        st.write(user)

    # Add new user
    st.subheader("Add New User")
    new_username = st.text_input("New Username")
    new_password = st.text_input("New Password", type="password")
    if st.button("Add User"):
        if new_username and new_password:
            USER_DB[new_username] = sha256(new_password.encode()).hexdigest()
            st.success(f"User {new_username} added successfully!")
        else:
            st.error("Please provide both username and password.")

    # Remove user
    st.subheader("Remove User")
    user_to_remove = st.selectbox("Select User to Remove", list(USER_DB.keys()))
    if st.button("Remove User"):
        if user_to_remove:
            del USER_DB[user_to_remove]
            st.success(f"User {user_to_remove} removed successfully!")


def paginate_dataframe(df: pd.DataFrame, page_size: int, key: str):
    """Paginate a DataFrame for display in Streamlit."""
    total_pages = (len(df) - 1) // page_size + 1
    page = st.number_input(
        "Page", min_value=1, max_value=total_pages, value=1, step=1, key=key
    )
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    return df.iloc[start_idx:end_idx]


def bet_history_tab():
    """Tab for viewing bet history by user and week with pagination."""
    st.header("Bet History")

    players = get_players()
    if players.empty:
        st.warning("No players found.")
        return

    search_query = st.text_input("Search Player", placeholder="Enter player name", key="bet_history_search")
    filtered_players = players[players['name'].str.contains(search_query, case=False)] if search_query else players

    col1, col2 = st.columns(2)

    with col1:
        player_name = st.selectbox("Player", filtered_players['name'].tolist(), key="bet_history_player")
        player_id = filtered_players[filtered_players['name'] == player_name]['id'].iloc[0]

    with col2:
        week = st.selectbox("Week", list(range(1, 19)), key="bet_history_week")

    # Fetch bets for the selected player and week
    bets_df = get_bets(week=week, player_id=player_id)

    if bets_df.empty:
        st.info("No bets found for the selected player and week.")
        return

    st.subheader(f"{player_name}'s Bets - Week {week}")

    # Calculate points for display
    bets_df['points'] = bets_df.apply(
        lambda row: bet_points(row['american_odds'], row['result'], 
                             bool(row['is_triple']), row['stake']), axis=1
    )

    # Format for display
    display_df = bets_df[['description', 'american_odds', 'stake', 'is_triple', 'result', 'points']].copy()
    display_df['is_triple'] = display_df['is_triple'].map({1: '✅', 0: ''})
    display_df['points'] = display_df['points'].round(1)
    display_df.columns = ['Description', 'Odds', 'Stake', 'Triple', 'Result', 'Points']

    paginated_df = paginate_dataframe(display_df, page_size=10, key="bet_history_pagination")
    st.dataframe(paginated_df, use_container_width=True)


def clear_all_results():
    """Clear all results from the bets table."""
    conn = sqlite3.connect('nfl_bets.db')
    cursor = conn.cursor()

    cursor.execute("UPDATE bets SET result = 'pending', updated_at = CURRENT_TIMESTAMP")

    conn.commit()
    conn.close()


def delete_all_bets():
    """Delete all bets from the database."""
    conn = sqlite3.connect('nfl_bets.db')
    cursor = conn.cursor()

    cursor.execute("DELETE FROM bets")

    conn.commit()
    conn.close()


def visualize_data_tab():
    """Tab for visualizing betting trends and player performance."""
    st.header("Data Visualization")

    # Fetch data
    bets_df = get_bets()
    players = get_players()

    if bets_df.empty:
        st.warning("No betting data available for visualization.")
        return

    # Weekly results visualization
    st.subheader("Weekly Results")
    weekly_results = bets_df.groupby('week')['result'].value_counts().unstack(fill_value=0)
    st.bar_chart(weekly_results)

    # Player performance visualization
    st.subheader("Player Performance")
    player_performance = bets_df.groupby('player_id')['result'].value_counts().unstack(fill_value=0)
    player_performance.index = players.set_index('id').loc[player_performance.index]['name']
    st.line_chart(player_performance)


def historical_analysis_tab():
    """Tab for analyzing historical betting performance."""
    st.header("Historical Analysis")

    # Fetch data
    bets_df = get_bets()

    if bets_df.empty:
        st.warning("No betting data available for analysis.")
        return

    # Calculate win/loss ratios
    st.subheader("Win/Loss Ratios")
    results_count = bets_df['result'].value_counts()
    win_loss_ratio = results_count.get('win', 0) / max(results_count.get('loss', 1), 1)
    st.metric(label="Win/Loss Ratio", value=f"{win_loss_ratio:.2f}")

    # Calculate ROI
    st.subheader("Return on Investment (ROI)")
    total_stake = bets_df['stake'].sum()
    total_profit = bets_df[bets_df['result'] == 'win']['stake'].sum() * bets_df[bets_df['result'] == 'win']['american_odds'].sum() / 100
    roi = (total_profit - total_stake) / max(total_stake, 1) * 100
    st.metric(label="ROI (%)", value=f"{roi:.2f}%")

    # Display results over time
    st.subheader("Results Over Time")
    results_over_time = bets_df.groupby('week')['result'].value_counts().unstack(fill_value=0)
    st.line_chart(results_over_time)


if __name__ == "__main__":
    main()