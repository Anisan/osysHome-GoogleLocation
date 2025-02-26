from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, IntegerField
from wtforms.validators import DataRequired

# Определение класса формы
class SettingsForm(FlaskForm):
    timeout = IntegerField('Timeout', validators=[DataRequired()])
    limit_speed_min = IntegerField('Limit speed min', validators=[DataRequired()])
    limit_speed_max = IntegerField('Limit speed max', validators=[DataRequired()])
    submit = SubmitField('Submit')
