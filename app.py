from flask import Flask, render_template, request, redirect, url_for, flash, session
import itertools
import random
from datetime import datetime
import mysql.connector
from mysql.connector import Error
import json
import os
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.units import inch
from flask import send_file
from io import BytesIO

app = Flask(__name__)
app.secret_key = 'carrom_scheduler_secret_key'

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', 'Darshan@2003'),
    'database': os.getenv('DB_NAME', 'carrom_tournament'),
    'port': int(os.getenv('DB_PORT', 3306))
}

def get_db_connection():
    """Create and return a database connection"""
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        return connection
    except Error as e:
        print(f"Error connecting to MySQL: {e}")
        return None

def init_database():
    """Initialize the database with required tables"""
    connection = get_db_connection()
    if not connection:
        return False
    
    try:
        cursor = connection.cursor()
        
        # Create tournaments table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tournaments (
                id VARCHAR(50) PRIMARY KEY,
                players TEXT NOT NULL,
                matches_per_team INT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create teams table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id INT AUTO_INCREMENT PRIMARY KEY,
                tournament_id VARCHAR(50) NOT NULL,
                name VARCHAR(100) NOT NULL,
                players TEXT NOT NULL,
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            )
        """)
        
        # Create matches table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INT AUTO_INCREMENT PRIMARY KEY,
                tournament_id VARCHAR(50) NOT NULL,
                match_id INT NOT NULL,
                team1 VARCHAR(100) NOT NULL,
                team2 VARCHAR(100) NOT NULL,
                status ENUM('scheduled', 'completed') DEFAULT 'scheduled',
                winner VARCHAR(100) NULL,
                won_with_queen BOOLEAN DEFAULT FALSE,
                date_scheduled DATETIME DEFAULT CURRENT_TIMESTAMP,
                date_completed DATETIME NULL,
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            )
        """)
        
        # Create scoreboard table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scoreboard (
                id INT AUTO_INCREMENT PRIMARY KEY,
                tournament_id VARCHAR(50) NOT NULL,
                team_name VARCHAR(100) NOT NULL,
                wins INT DEFAULT 0,
                queen_wins INT DEFAULT 0,
                total_points DECIMAL(5,2) DEFAULT 0.00,
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                UNIQUE KEY unique_tournament_team (tournament_id, team_name)
            )
        """)
        
        connection.commit()
        print("Database initialized successfully")
        return True
        
    except Error as e:
        print(f"Error initializing database: {e}")
        return False
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

class Tournament:
    def __init__(self, tournament_id, players, matches_per_team):
        self.id = tournament_id
        self.players = players
        self.matches_per_team = matches_per_team
        self.teams = self.create_teams()
        self.matches = []
        self.scoreboard = {}
        self.completed_matches = []
        
        # Save tournament to database
        self.save_to_db()
        
    def create_teams(self):
        shuffled_players = self.players.copy()
        random.shuffle(shuffled_players)
        teams = []
        for i in range(0, len(shuffled_players), 2):
            team_name = f"Team {chr(65 + i//2)}"
            teams.append({
                'name': team_name,
                'players': [shuffled_players[i], shuffled_players[i+1]]
            })
        return teams
    
    def save_to_db(self):
        """Save tournament data to database"""
        connection = get_db_connection()
        if not connection:
            return False
        
        try:
            cursor = connection.cursor()
            
            # Insert tournament
            cursor.execute("""
                INSERT INTO tournaments (id, players, matches_per_team) 
                VALUES (%s, %s, %s)
            """, (self.id, json.dumps(self.players), self.matches_per_team))
            
            # Insert teams
            for team in self.teams:
                cursor.execute("""
                    INSERT INTO teams (tournament_id, name, players) 
                    VALUES (%s, %s, %s)
                """, (self.id, team['name'], json.dumps(team['players'])))
            
            # Initialize scoreboard
            for team in self.teams:
                cursor.execute("""
                    INSERT INTO scoreboard (tournament_id, team_name, wins, queen_wins, total_points) 
                    VALUES (%s, %s, 0, 0, 0.00)
                """, (self.id, team['name']))
            
            # Schedule matches
            self.schedule_matches(cursor)
            
            connection.commit()
            return True
            
        except Error as e:
            print(f"Error saving tournament to database: {e}")
            connection.rollback()
            return False
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()
    
    def schedule_matches(self, cursor):
        """Schedule matches and save to database"""
        team_names = [team['name'] for team in self.teams]
        all_combinations = list(itertools.combinations(team_names, 2))
        
        # Calculate how many times each combination should be played
        total_teams = len(team_names)
        total_matches_needed = (total_teams * self.matches_per_team) // 2
        matches_per_combination = total_matches_needed // len(all_combinations)
        extra_matches = total_matches_needed % len(all_combinations)
        
        match_id = 1
        
        # Add regular matches
        for combination in all_combinations:
            for _ in range(matches_per_combination):
                cursor.execute("""
                    INSERT INTO matches (tournament_id, match_id, team1, team2, status, date_scheduled) 
                    VALUES (%s, %s, %s, %s, 'scheduled', %s)
                """, (self.id, match_id, combination[0], combination[1], datetime.now()))
                match_id += 1
        
        # Add extra matches to balance
        for i in range(extra_matches):
            combination = all_combinations[i % len(all_combinations)]
            cursor.execute("""
                INSERT INTO matches (tournament_id, match_id, team1, team2, status, date_scheduled) 
                VALUES (%s, %s, %s, %s, 'scheduled', %s)
            """, (self.id, match_id, combination[0], combination[1], datetime.now()))
            match_id += 1
    
    @staticmethod
    def load_from_db(tournament_id):
        """Load tournament data from database"""
        connection = get_db_connection()
        if not connection:
            return None
        
        try:
            cursor = connection.cursor(dictionary=True)
            
            # Get tournament data
            cursor.execute("SELECT * FROM tournaments WHERE id = %s", (tournament_id,))
            tournament_data = cursor.fetchone()
            
            if not tournament_data:
                return None
            
            # Create tournament object
            tournament = Tournament.__new__(Tournament)
            tournament.id = tournament_data['id']
            tournament.players = json.loads(tournament_data['players'])
            tournament.matches_per_team = tournament_data['matches_per_team']
            
            # Load teams
            cursor.execute("SELECT * FROM teams WHERE tournament_id = %s", (tournament_id,))
            teams_data = cursor.fetchall()
            tournament.teams = []
            for team_data in teams_data:
                tournament.teams.append({
                    'name': team_data['name'],
                    'players': json.loads(team_data['players'])
                })
            
            # Load matches
            cursor.execute("SELECT * FROM matches WHERE tournament_id = %s ORDER BY match_id", (tournament_id,))
            matches_data = cursor.fetchall()
            tournament.matches = []
            tournament.completed_matches = []
            
            for match_data in matches_data:
                match = {
                    'id': match_data['match_id'],
                    'team1': match_data['team1'],
                    'team2': match_data['team2'],
                    'status': match_data['status'],
                    'winner': match_data['winner'],
                    'won_with_queen': bool(match_data['won_with_queen']),
                    'date_scheduled': match_data['date_scheduled'].strftime('%Y-%m-%d %H:%M'),
                    'date_completed': match_data['date_completed'].strftime('%Y-%m-%d %H:%M') if match_data['date_completed'] else None
                }
                tournament.matches.append(match)
                
                if match['status'] == 'completed':
                    tournament.completed_matches.append(match)
            
            # Load scoreboard
            cursor.execute("SELECT * FROM scoreboard WHERE tournament_id = %s", (tournament_id,))
            scoreboard_data = cursor.fetchall()
            tournament.scoreboard = {}
            for score_data in scoreboard_data:
                tournament.scoreboard[score_data['team_name']] = {
                    'wins': score_data['wins'],
                    'queen_wins': score_data['queen_wins'],
                    'total_points': float(score_data['total_points'])
                }
            
            return tournament
            
        except Error as e:
            print(f"Error loading tournament from database: {e}")
            return None
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()
    
    def update_match_result(self, match_id, winner, won_with_queen):
        """Update match result in database"""
        connection = get_db_connection()
        if not connection:
            return False
        
        try:
            cursor = connection.cursor()
            
            # Update match
            cursor.execute("""
    UPDATE matches 
    SET status = 'completed', winner = %s, won_with_queen = %s, date_completed = %s 
    WHERE tournament_id = %s AND match_id = %s
""", (winner, won_with_queen, datetime.now(), self.id, match_id))

            
            # Update scoreboard
            if won_with_queen:
                cursor.execute("""
                    UPDATE scoreboard 
                    SET queen_wins = queen_wins + 1, total_points = total_points + 1.5 
                    WHERE tournament_id = %s AND team_name = %s
                """, (self.id, winner))
            else:
                cursor.execute("""
                    UPDATE scoreboard 
                    SET wins = wins + 1, total_points = total_points + 1 
                    WHERE tournament_id = %s AND team_name = %s
                """, (self.id, winner))
            
            connection.commit()
            
            # Update local objects
            for match in self.matches:
                if match['id'] == match_id:
                    match['status'] = 'completed'
                    match['winner'] = winner
                    match['won_with_queen'] = won_with_queen
                    match['date_completed'] = datetime.now().strftime('%Y-%m-%d %H:%M')
                    
                    # Update local scoreboard
                    if won_with_queen:
                        self.scoreboard[winner]['queen_wins'] += 1
                        self.scoreboard[winner]['total_points'] += 1.5
                    else:
                        self.scoreboard[winner]['wins'] += 1
                        self.scoreboard[winner]['total_points'] += 1
                    
                    self.completed_matches.append(match.copy())
                    break
            
            return True
            
        except Error as e:
            print(f"Error updating match result: {e}")
            connection.rollback()
            return False
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create_tournament', methods=['POST'])
def create_tournament():
    players_input = request.form.get('players', '').strip()
    matches_per_team = int(request.form.get('matches_per_team', 2))
    
    if not players_input:
        flash('Please enter player names', 'error')
        return redirect(url_for('index'))
    
    players = [name.strip() for name in players_input.split(',') if name.strip()]
    
    if len(players) < 4:
        flash('Need at least 4 players', 'error')
        return redirect(url_for('index'))
    
    if len(players) % 2 != 0:
        flash('Number of players must be even', 'error')
        return redirect(url_for('index'))
    
    tournament_id = f"tournament_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tournament = Tournament(tournament_id, players, matches_per_team)
    
    flash(f'Tournament created successfully with {len(players)} players!', 'success')
    return redirect(url_for('tournament_dashboard', tournament_id=tournament_id))

@app.route('/tournament/<tournament_id>')
def tournament_dashboard(tournament_id):
    tournament = Tournament.load_from_db(tournament_id)
    if not tournament:
        flash('Tournament not found', 'error')
        return redirect(url_for('index'))
    
    return render_template('tournament.html', tournament=tournament)

@app.route('/match_result/<tournament_id>/<int:match_id>', methods=['POST'])
def update_match_result(tournament_id, match_id):
    tournament = Tournament.load_from_db(tournament_id)
    if not tournament:
        flash('Tournament not found', 'error')
        return redirect(url_for('index'))
    
    winner = request.form.get('winner')
    won_with_queen = request.form.get('won_with_queen') == 'on'
    
    if winner:
        if tournament.update_match_result(match_id, winner, won_with_queen):
            flash('Match result updated successfully!', 'success')
        else:
            flash('Error updating match result', 'error')
    
    return redirect(url_for('tournament_dashboard', tournament_id=tournament_id))

@app.route('/scoreboard/<tournament_id>')
def scoreboard(tournament_id):
    tournament = Tournament.load_from_db(tournament_id)
    if not tournament:
        flash('Tournament not found', 'error')
        return redirect(url_for('index'))
    
    # Sort teams by total points (descending)
    sorted_scoreboard = sorted(tournament.scoreboard.items(), 
                              key=lambda x: x[1]['total_points'], reverse=True)
    
    return render_template('scoreboard.html', 
                         tournament=tournament, 
                         sorted_scoreboard=sorted_scoreboard)

@app.route('/tournaments')
def list_tournaments():
    """List all tournaments"""
    connection = get_db_connection()
    if not connection:
        flash('Database connection error', 'error')
        return redirect(url_for('index'))
    
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, players, matches_per_team, created_at 
            FROM tournaments 
            ORDER BY created_at DESC
        """)
        tournaments = cursor.fetchall()
        
        # Parse players JSON for display
        for tournament in tournaments:
            tournament['players'] = json.loads(tournament['players'])
        
        return render_template('tournaments.html', tournaments=tournaments)
        
    except Error as e:
        print(f"Error fetching tournaments: {e}")
        flash('Error fetching tournaments', 'error')
        return redirect(url_for('index'))
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

# Add this route to your Flask app

@app.route('/reset_match/<tournament_id>/<int:match_id>', methods=['POST'])
def reset_match_result(tournament_id, match_id):
    tournament = Tournament.load_from_db(tournament_id)
    if not tournament:
        flash('Tournament not found', 'error')
        return redirect(url_for('index'))
    
    # Find the match to reset
    match_to_reset = None
    for match in tournament.matches:
        if match['id'] == match_id and match['status'] == 'completed':
            match_to_reset = match
            break
    
    if not match_to_reset:
        flash('Match not found or not completed', 'error')
        return redirect(url_for('tournament_dashboard', tournament_id=tournament_id))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error', 'error')
        return redirect(url_for('tournament_dashboard', tournament_id=tournament_id))
    
    try:
        cursor = connection.cursor()
        
        # Reset match status in database
        cursor.execute("""
            UPDATE matches 
            SET status = 'scheduled', winner = NULL, won_with_queen = FALSE, date_completed = NULL 
            WHERE tournament_id = %s AND match_id = %s
        """, (tournament_id, match_id))
        
        # Revert scoreboard changes
        winner = match_to_reset['winner']
        won_with_queen = match_to_reset['won_with_queen']
        
        if won_with_queen:
            cursor.execute("""
                UPDATE scoreboard 
                SET queen_wins = queen_wins - 1, total_points = total_points - 1.5 
                WHERE tournament_id = %s AND team_name = %s
            """, (tournament_id, winner))
        else:
            cursor.execute("""
                UPDATE scoreboard 
                SET wins = wins - 1, total_points = total_points - 1 
                WHERE tournament_id = %s AND team_name = %s
            """, (tournament_id, winner))
        
        connection.commit()
        flash('Match result reset successfully!', 'success')
        
    except Error as e:
        print(f"Error resetting match result: {e}")
        connection.rollback()
        flash('Error resetting match result', 'error')
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    
    return redirect(url_for('tournament_dashboard', tournament_id=tournament_id))

@app.route('/download_pdf/<tournament_id>')
def download_pdf(tournament_id):
    tournament = Tournament.load_from_db(tournament_id)
    if not tournament:
        flash('Tournament not found', 'error')
        return redirect(url_for('index'))
    
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    margin = 50
    box_width = width - 2 * margin
    y = height - 60
    
    def draw_box(title, items, y_start, row_height=30):
        nonlocal p
        y = y_start
        
        # Draw section title (left-aligned)
        p.setFont("Helvetica-Bold", 14)
        p.drawString(margin, y, title)
        y -= 25
        
        box_top = y
        box_height = len(items) * row_height + 20
        box_bottom = y - box_height + 20
        
        # Draw outer box
        p.setLineWidth(1)
        p.setStrokeColor(colors.black)
        p.rect(margin, box_bottom, box_width, box_height)
        
        y = box_top - 10
        p.setFont("Helvetica", 10)
        
        # Draw items with left-aligned text
        for i, item in enumerate(items):
            text_x = margin + 10  # Left-aligned with padding
            p.drawString(text_x, y, item)
            y -= row_height
            
            # Draw separator lines (except for last item)
            if i < len(items) - 1:
                p.setStrokeColor(colors.lightgrey)
                p.setLineWidth(0.5)
                p.line(margin + 10, y + row_height / 2, width - margin - 10, y + row_height / 2)
        
        return box_bottom - 40  # space before next box
    
    # Main Title (centered)
    p.setFont("Helvetica-Bold", 22)
    p.drawCentredString(width / 2, y, "Carrom Tournament")
    
    # Add tournament name if available
    if hasattr(tournament, 'name') and tournament.name:
        y -= 25
        p.setFont("Helvetica", 14)
        p.drawCentredString(width / 2, y, tournament.name)
    
    y -= 50
    
    # Teams Section
    team_lines = []
    for i, team in enumerate(tournament.teams, 1):
        team_line = f"Team {i}: {team['name']}"
        if team['players']:
            team_line += f" - Players: {', '.join(team['players'])}"
        team_lines.append(team_line)
    
    y = draw_box("Teams", team_lines, y)
    
    # Match Schedule Section
    match_lines = []
    for match in tournament.matches:
        match_line = f"Match {match['id']}: {match['team1']} vs {match['team2']}"
        
        # Add status information
        if match['status'] == 'completed':
            match_line += f" | Winner: {match['winner']}"
            if match.get('won_with_queen', False):
                match_line += " (with Queen)"
        elif match['status'] == 'in_progress':
            match_line += " | Status: In Progress"
        else:
            match_line += " | Status: Pending"
        
        match_lines.append(match_line)
    
    # Check if we need a new page
    estimated_height = len(match_lines) * 30 + 80
    if y < estimated_height:
        p.showPage()
        y = height - 60
    
    y = draw_box("Match Schedule", match_lines, y)
    
    # Add footer with generation date
    p.setFont("Helvetica", 8)
    p.setFillColor(colors.grey)
    from datetime import datetime
    footer_text = f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    p.drawString(margin, 30, footer_text)
    
    # Finalize PDF
    p.showPage()
    p.save()
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"carrom_tournament_{tournament_id}.pdf",
        mimetype='application/pdf'
    )

@app.route('/download_scoreboard/<tournament_id>')
def download_scoreboard(tournament_id):
    tournament = Tournament.load_from_db(tournament_id)
    if not tournament:
        flash('Tournament not found', 'error')
        return redirect(url_for('index'))
    
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    margin = 50
    box_width = width - 2 * margin
    y = height - 60
    
    def draw_table(title, headers, rows, y_start, row_height=30):
        """Draw a table with proper Excel-like structure"""
        nonlocal p
        y = y_start
        
        # Draw section title
        p.setFont("Helvetica-Bold", 14)
        p.drawCentredString(width / 2, y, title)
        y -= 30
        
        # Calculate column widths
        num_cols = len(headers)
        col_widths = [box_width / num_cols] * num_cols
        
        # Adjust column widths for better content fit
        if num_cols == 5:  # Rank, Team, Wins, Queen Wins, Total Points
            col_widths = [60, box_width - 300, 80, 80, 80]
        
        table_height = (len(rows) + 1) * row_height  # +1 for header
        table_top = y
        table_bottom = y - table_height
        
        # Draw outer table border
        p.setStrokeColor(colors.black)
        p.setLineWidth(2)
        p.rect(margin, table_bottom, box_width, table_height)
        
        # Draw header row
        current_y = table_top - row_height
        p.setFillColor(colors.lightgrey)
        p.rect(margin, current_y, box_width, row_height, fill=1)
        
        # Draw header text
        p.setFillColor(colors.black)
        p.setFont("Helvetica-Bold", 11)
        current_x = margin
        for i, header in enumerate(headers):
            text_x = current_x + col_widths[i] / 2
            text_y = current_y + row_height / 2 - 4
            p.drawCentredString(text_x, text_y, header)
            current_x += col_widths[i]
        
        # Draw vertical lines for header
        p.setStrokeColor(colors.black)
        p.setLineWidth(1)
        current_x = margin
        for i in range(num_cols + 1):
            p.line(current_x, table_top, current_x, table_bottom)
            if i < num_cols:
                current_x += col_widths[i]
        
        # Draw data rows
        p.setFillColor(colors.black)
        p.setFont("Helvetica", 10)
        
        for row_idx, row_data in enumerate(rows):
            current_y = table_top - (row_idx + 2) * row_height
            
            # Alternate row colors for better readability
            if row_idx % 2 == 0:
                p.setFillColor(colors.white)
            else:
                p.setFillColor(colors.Color(0.95, 0.95, 0.95))
            
            p.rect(margin, current_y, box_width, row_height, fill=1)
            
            # Draw row text
            p.setFillColor(colors.black)
            current_x = margin
            for i, cell_data in enumerate(row_data):
                text_x = current_x + col_widths[i] / 2
                text_y = current_y + row_height / 2 - 4
                
                # Right-align numeric columns
                if i in [2, 3, 4]:  # Wins, Queen Wins, Total Points
                    text_x = current_x + col_widths[i] - 10
                    p.drawRightString(text_x, text_y, str(cell_data))
                elif i == 0:  # Rank - center align
                    p.drawCentredString(text_x, text_y, str(cell_data))
                else:  # Team name - left align
                    text_x = current_x + 10
                    p.drawString(text_x, text_y, str(cell_data))
                
                current_x += col_widths[i]
            
            # Draw horizontal line after each row
            p.setStrokeColor(colors.black)
            p.setLineWidth(0.5)
            p.line(margin, current_y, margin + box_width, current_y)
        
        # Draw vertical lines for all columns
        p.setStrokeColor(colors.black)
        p.setLineWidth(1)
        current_x = margin
        for i in range(num_cols + 1):
            p.line(current_x, table_top, current_x, table_bottom)
            if i < num_cols:
                current_x += col_widths[i]
        
        return table_bottom - 40  # return next starting y position
    
    # Title
    p.setFont("Helvetica-Bold", 20)
    p.drawCentredString(width / 2, y, "Carrom Tournament - Scoreboard")
    y -= 60
    
    # Sort scoreboard by total points (descending)
    sorted_scores = sorted(
        tournament.scoreboard.items(),
        key=lambda x: x[1]['total_points'],
        reverse=True
    )
    
    # Prepare table data
    headers = ["Rank", "Team Name", "Wins", "Queen Wins", "Total Points"]
    table_rows = []
    
    for idx, (team_name, stats) in enumerate(sorted_scores, start=1):
        row = [
            idx,
            team_name,
            stats['wins'],
            stats['queen_wins'],
            f"{stats['total_points']:.2f}"
        ]
        table_rows.append(row)
    
    # Check if we need a new page
    estimated_height = (len(table_rows) + 1) * 30 + 100  # +1 for header, +100 for margins
    if y < estimated_height:
        p.showPage()
        y = height - 60
        # Redraw title on new page
        p.setFont("Helvetica-Bold", 20)
        p.drawCentredString(width / 2, y, "Carrom Tournament - Scoreboard")
        y -= 60
    
    # Draw the table
    draw_table("Tournament Rankings", headers, table_rows, y)
    
    # Add footer with generation timestamp
    p.setFont("Helvetica", 8)
    p.setFillColor(colors.grey)
    footer_text = f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    p.drawString(margin, 30, footer_text)
    
    # Finalize PDF
    p.showPage()
    p.save()
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"{tournament.id}_scoreboard.pdf",
        mimetype='application/pdf'
    )

if __name__ == '__main__':
    # Initialize database on startup
    if init_database():
        print("Starting Flask application...")
        app.run(debug=True)
    else:
        print("Failed to initialize database. Please check your MySQL connection.")