from flask_wtf import  FlaskForm
from wtforms import StringField, SubmitField, BooleanField
from wtforms.validators import DataRequired, Optional


class LocationForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired()])
    sendtogps = BooleanField('Send to gps',validators=[Optional()])
    submit = SubmitField('Submit')